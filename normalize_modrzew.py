#!/usr/bin/env python3
"""Create a consistent copy of MoDrzew attachments using parent tree FIDs."""

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import warnings


MAX_BYTES = 5_000_000
ATTACHMENTS = {
    "drzewa_zalaczniki": ("K_(2,3", "POINT"),
    "drzewa_umiar_ryz_zalaczniki": ("K_(1)", "POLYGON"),
}
NAME_PATTERN = re.compile(r"([^_]+)_([1-9][0-9]*)_([0-9]{8})_(.+)")
UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class NormalizationError(Exception):
    pass


@dataclass
class Conversion:
    table: str
    attachment_fid: int
    tree_fid: int
    old_name: str
    new_name: str
    source: Path | None


@dataclass
class Plan:
    conversions: list[Conversion] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


def readonly_database(path):
    # mode=ro respects WAL; immutable=1 would ignore uncheckpointed changes.
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)


def check_integrity(connection):
    result = connection.execute("PRAGMA integrity_check").fetchall()
    if result != [("ok",)]:
        raise NormalizationError(f"Niepoprawna baza SQLite: {result}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise NormalizationError("Baza zawiera niepoprawne klucze obce.")


def require_columns(connection, table, required):
    columns = {row[1] for row in connection.execute(
        f"PRAGMA table_info({identifier(table)})"
    )}
    missing = required - columns
    if missing:
        raise NormalizationError(
            f"Tabela {table}: brak kolumn {', '.join(sorted(missing))}."
        )


def safe_filename(name):
    if (not name or UNSAFE_CHARS.search(name) or name.endswith((" ", "."))
            or RESERVED.match(name) or len(name.encode("utf-16-le")) // 2 > 255):
        raise NormalizationError(f"Niedozwolona nazwa pliku Windows: {name!r}.")


def target_name(old_name, tree_fid, inventory):
    if not isinstance(old_name, str):
        raise NormalizationError("Pusta lub nietekstowa nazwa zalacznika.")
    safe_filename(old_name)
    if old_name.lower().endswith((".jpg", ".jpeg")):
        raise NormalizationError(
            f"Referencja powinna byc bez rozszerzenia: {old_name!r}."
        )
    match = NAME_PATTERN.fullmatch(old_name)
    if not match:
        raise NormalizationError(
            f"Niepoprawny wzorzec nazwy (identyfikator_numer_data_opis): {old_name!r}."
        )
    prefix, _, date, _ = match.groups()
    try:
        datetime.strptime(date, "%Y%m%d")
    except ValueError as exc:
        raise NormalizationError(f"Niepoprawna data w nazwie {old_name!r}.") from exc
    if not isinstance(tree_fid, int) or tree_fid < 1:
        raise NormalizationError(f"Niepoprawny FID drzewa: {tree_fid!r}.")
    if prefix not in {str(tree_fid), str(inventory)}:
        raise NormalizationError(
            f"Nazwa {old_name!r} nie odpowiada FID {tree_fid} "
            f"ani numerowi inwentaryzacyjnemu {inventory!r}."
        )
    new_name = f"{tree_fid}_{old_name.split('_', 1)[1]}"
    safe_filename(new_name + ".jpg")
    return new_name


def build_plan(connection, images):
    check_integrity(connection)
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if not {"gpkg_contents", "gpkg_geometry_columns"} <= tables:
        raise NormalizationError("Plik nie ma wymaganych tabel GeoPackage.")
    layers = connection.execute(
        "SELECT g.table_name, g.geometry_type_name FROM gpkg_geometry_columns g "
        "JOIN gpkg_contents c ON c.table_name=g.table_name "
        "WHERE c.data_type='features'"
    ).fetchall()
    if not (tables & ATTACHMENTS.keys()):
        raise NormalizationError("Brak obslugiwanych tabel zalacznikow MoDrzew.")

    sources = defaultdict(list)
    image_files = []
    for path in sorted(images.iterdir(), key=lambda p: p.name):
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}:
            if path.is_symlink():
                raise NormalizationError(f"Dowiazanie zamiast zdjecia: {path.name}.")
            sources[path.stem].append(path)
            image_files.append(path)

    plan = Plan()
    used = set()
    targets = {}
    for table, (prefix, geometry) in ATTACHMENTS.items():
        if table not in tables:
            continue
        require_columns(connection, table, {"fid", "fid_drzewo", "nazwa_pliku"})
        if not connection.execute(f"SELECT 1 FROM {identifier(table)} LIMIT 1").fetchone():
            continue
        parents = [
            name for name, kind in layers
            if name.startswith(prefix) and kind.upper() == geometry
        ]
        if len(parents) != 1:
            plan.errors.append(
                f"Tabela {table}: oczekiwano jednej warstwy nadrzednej; "
                f"znaleziono {len(parents)}."
            )
            continue
        parent = parents[0]
        require_columns(connection, parent, {"fid", "numer_inw"})
        rows = connection.execute(
            f"SELECT a.fid, a.fid_drzewo, a.nazwa_pliku, p.fid, p.numer_inw "
            f"FROM {identifier(table)} a LEFT JOIN {identifier(parent)} p "
            "ON p.fid=a.fid_drzewo ORDER BY a.fid"
        )
        for attachment_fid, linked_fid, old_name, tree_fid, inventory in rows:
            context = f"{table}, zalacznik {attachment_fid}"
            try:
                if tree_fid is None:
                    raise NormalizationError(f"Brak drzewa FID {linked_fid!r}.")
                new_name = target_name(old_name, tree_fid, inventory)
                candidates = sources.get(old_name, [])
                used.update(candidates)
                source = candidates[0] if len(candidates) == 1 else None
                if not candidates:
                    plan.errors.append(f"{context}: brak zdjecia {old_name}.jpg.")
                elif len(candidates) > 1:
                    plan.errors.append(
                        f"{context}: wiele plikow dla {old_name!r}: "
                        + ", ".join(p.name for p in candidates)
                    )
                key = (new_name + ".jpg").casefold()
                current = (new_name, source, old_name)
                if key in targets and targets[key] != current:
                    raise NormalizationError(
                        f"Kolizja nazwy wynikowej Windows: {new_name}.jpg."
                    )
                targets[key] = current
                plan.conversions.append(Conversion(
                    table, attachment_fid, tree_fid, old_name, new_name, source
                ))
            except NormalizationError as exc:
                plan.errors.append(f"{context}: {exc}")
    plan.unmatched = [p.name for p in image_files if p not in used]
    return plan


def pillow_modules():
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise NormalizationError(
            "Brak Pillow. Aktywuj venv i wykonaj: "
            "python3 -m pip install -r requirements.txt"
        ) from exc
    return Image, ImageOps


def decode_jpeg(path):
    Image, _ = pillow_modules()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.format != "JPEG":
                raise NormalizationError(f"Plik nie jest JPEG: {path.name}.")
            image.load()
            return image.copy()


def process_image(source, destination):
    Image, ImageOps = pillow_modules()
    image = decode_jpeg(source)
    try:
        source_size = source.stat().st_size
        source_dimensions = image.size
        if source_size <= MAX_BYTES:
            shutil.copyfile(source, destination)
            action = "copied"
        else:
            oriented = ImageOps.exif_transpose(image)
            rgb = oriented.convert("RGB")
            try:
                metadata = {}
                exif = oriented.getexif()
                if exif:
                    metadata["exif"] = exif.tobytes()
                # Retain an RGB profile only if the original pixel space was RGB.
                if oriented.mode == "RGB" and oriented.info.get("icc_profile"):
                    metadata["icc_profile"] = oriented.info["icc_profile"]
                if oriented.info.get("dpi"):
                    metadata["dpi"] = oriented.info["dpi"]
                for _ in range(24):
                    for quality in (95, 85, 75, 65, 55, 45, 35, 25):
                        buffer = io.BytesIO()
                        rgb.save(buffer, format="JPEG", quality=quality,
                                 optimize=True, **metadata)
                        if buffer.tell() <= MAX_BYTES:
                            destination.write_bytes(buffer.getvalue())
                            action = "recompressed"
                            break
                    else:
                        width, height = rgb.size
                        dimensions = (max(1, round(width * 0.8)),
                                      max(1, round(height * 0.8)))
                        if dimensions == rgb.size:
                            break
                        smaller = rgb.resize(dimensions, Image.Resampling.LANCZOS)
                        rgb.close()
                        rgb = smaller
                        continue
                    break
                else:
                    raise NormalizationError(f"Nie udalo sie zmniejszyc {source.name}.")
                if not destination.is_file():
                    raise NormalizationError(
                        f"Nie udalo sie osiagnac limitu 5 MB: {source.name}."
                    )
            finally:
                rgb.close()
                oriented.close()
        if destination.stat().st_size > MAX_BYTES:
            raise NormalizationError(f"Wynik przekracza 5 MB: {destination.name}.")
        verified = decode_jpeg(destination)
        try:
            return {
                "source": source.name,
                "output": destination.name,
                "source_bytes": source_size,
                "output_bytes": destination.stat().st_size,
                "source_dimensions": list(source_dimensions),
                "output_dimensions": list(verified.size),
                "action": action,
            }
        finally:
            verified.close()
    finally:
        image.close()


def validate_paths(gpkg, images, output):
    if not gpkg.is_file() or not images.is_dir():
        raise NormalizationError("Wymagany plik GeoPackage i katalog zdjec nie istnieja.")
    if gpkg.name.casefold() in {"images", "report.json"}:
        raise NormalizationError("Nazwa GeoPackage koliduje ze struktura wynikow.")
    if output.exists() or output.is_symlink():
        raise NormalizationError(f"Katalog wynikowy juz istnieje: {output}.")
    if output == images or images in output.parents or output == gpkg:
        raise NormalizationError("Katalog wynikowy nie moze znajdowac sie w danych wejsciowych.")
    if not output.parent.is_dir():
        raise NormalizationError("Katalog nadrzedny wynikow musi juz istniec.")
    safe_filename(gpkg.name)


def publish(connection, gpkg, output, plan):
    if plan.errors:
        raise NormalizationError("Bledy planu blokuja utworzenie wynikow.")
    # Cooperating invocations use an exclusive sibling lock. No existing output
    # is replaced; users must not create/change the destination during a run.
    lock = output.with_name("." + output.name + ".lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise NormalizationError(f"Istnieje blokada wynikow: {lock}.") from exc
    staging = None
    try:
        os.close(lock_fd)
        if output.exists() or output.is_symlink():
            raise NormalizationError(f"Katalog wynikowy juz istnieje: {output}.")
        staging = Path(tempfile.mkdtemp(
            prefix=f".{output.name}.staging-", dir=output.parent
        ))
        image_output = staging / "images"
        image_output.mkdir()
        results = {}
        for item in plan.conversions:
            if item.new_name not in results:
                results[item.new_name] = process_image(
                    item.source, image_output / (item.new_name + ".jpg")
                )

        copied = sqlite3.connect(staging / gpkg.name)
        try:
            connection.backup(copied)
            copied.execute("PRAGMA journal_mode=DELETE")
            copied.execute("PRAGMA foreign_keys=ON")
            with copied:
                for item in plan.conversions:
                    cursor = copied.execute(
                        f"UPDATE {identifier(item.table)} SET nazwa_pliku=? "
                        "WHERE fid=? AND nazwa_pliku=?",
                        (item.new_name, item.attachment_fid, item.old_name),
                    )
                    if cursor.rowcount != 1:
                        raise NormalizationError("Zmiana danych podczas kopiowania bazy.")
            check_integrity(copied)
            expected = {(c.table, c.attachment_fid): c.new_name
                        for c in plan.conversions}
            actual = {}
            for table in ATTACHMENTS:
                if copied.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)
                ).fetchone():
                    for fid, name in copied.execute(
                        f"SELECT fid,nazwa_pliku FROM {identifier(table)}"
                    ):
                        actual[(table, fid)] = name
            if actual != expected:
                raise NormalizationError("Referencje w kopii nie odpowiadaja planowi.")
            expected_images = {name + ".jpg" for name in actual.values()}
            if {p.name for p in image_output.iterdir()} != expected_images:
                raise NormalizationError("Niekompletny katalog zdjec wynikowych.")
        finally:
            copied.close()
        report = {
            "max_image_bytes": MAX_BYTES,
            "unmatched_files": plan.unmatched,
            "conversions": [{
                "table": c.table, "attachment_fid": c.attachment_fid,
                "tree_fid": c.tree_fid, "old_name": c.old_name,
                "new_name": c.new_name, **results[c.new_name],
            } for c in plan.conversions],
        }
        (staging / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if output.exists() or output.is_symlink():
            raise NormalizationError(f"Katalog wynikowy juz istnieje: {output}.")
        staging.rename(output)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Kopia GeoPackage i zdjec MoDrzew z nazwami opartymi na FID drzewa."
    )
    parser.add_argument("gpkg", type=Path, help="plik GeoPackage (.gpkg)")
    parser.add_argument("images", type=Path, help="katalog zdjec (bez podkatalogow)")
    parser.add_argument("--apply", action="store_true",
                        help="utworz wyniki; domyslnie tylko podglad")
    parser.add_argument("--output-dir", type=Path, help="nowy katalog wynikowy")
    args = parser.parse_args(argv)
    gpkg, images = args.gpkg.resolve(), args.images.resolve()
    raw_output = args.output_dir or gpkg.with_name(gpkg.stem + "_normalized")
    output = raw_output.absolute()
    try:
        if output.is_symlink():
            raise NormalizationError("Katalog wynikowy nie moze byc dowiazaniem.")
        output = output.resolve()
        validate_paths(gpkg, images, output)
        connection = readonly_database(gpkg)
        try:
            connection.execute("BEGIN")
            plan = build_plan(connection, images)
            print(f"Zalaczniki w planie: {len(plan.conversions)}")
            for item in plan.conversions:
                status = ""
                if item.source and item.source.stat().st_size > MAX_BYTES:
                    status = " [wymaga kompresji]"
                print(f"  {item.old_name} -> {item.new_name}.jpg{status}")
            if plan.unmatched:
                print("Pliki bez referencji (bez zmian): " + ", ".join(plan.unmatched))
            if plan.errors:
                for error in plan.errors:
                    print(f"BLAD: {error}", file=sys.stderr)
                print("Nie utworzono pakietu wynikowego.", file=sys.stderr)
                return 1
            if args.apply:
                publish(connection, gpkg, output, plan)
                print(f"Utworzono pakiet: {output}")
            else:
                print("Podglad bez zapisu. Aby utworzyc wyniki, dodaj --apply.")
            return 0
        finally:
            connection.close()
    except Exception as exc:
        print(f"BLAD: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

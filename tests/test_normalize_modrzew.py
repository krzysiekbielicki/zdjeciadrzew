import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import normalize_modrzew as app


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gpkg = self.root / "park.gpkg"
        self.images = self.root / "sources"
        self.images.mkdir()
        self.output = self.root / "results"
        self.point = "K_(2,3, inwent)_eksport_modrzew_20991231"
        self.polygon = "K_(1)_eksport_modrzew_20991231"
        with self.database() as connection:
            connection.executescript("""
                PRAGMA application_id=1196444487;
                PRAGMA user_version=10200;
                CREATE TABLE gpkg_contents(table_name TEXT, data_type TEXT);
                CREATE TABLE gpkg_geometry_columns(
                    table_name TEXT, geometry_type_name TEXT
                );
                CREATE TABLE drzewa_zalaczniki(
                    fid INTEGER PRIMARY KEY, fid_drzewo INTEGER, nazwa_pliku TEXT
                );
                CREATE TABLE drzewa_umiar_ryz_zalaczniki(
                    fid INTEGER PRIMARY KEY, fid_drzewo INTEGER, nazwa_pliku TEXT
                );
                CREATE TABLE untouched(value TEXT);
                INSERT INTO untouched VALUES('unchanged');
            """)
            for name, geometry in [(self.point, "POINT"), (self.polygon, "POLYGON")]:
                connection.execute(
                    f"CREATE TABLE {app.identifier(name)} "
                    "(fid INTEGER PRIMARY KEY, numer_inw TEXT, id INTEGER, geometria BLOB)"
                )
                connection.execute("INSERT INTO gpkg_contents VALUES(?, 'features')",
                                   (name,))
                connection.execute("INSERT INTO gpkg_geometry_columns VALUES(?,?)",
                                   (name, geometry))
                connection.execute(
                    f"INSERT INTO {app.identifier(name)} VALUES(21,'D747994',909690,?)",
                    (b"untouched geometry",),
                )
        self.name = "D747994_2_20261016_Park_Ceramiczna"
        self.target = "21_2_20261016_Park_Ceramiczna"

    @contextlib.contextmanager
    def database(self, path=None):
        with contextlib.closing(sqlite3.connect(path or self.gpkg)) as connection:
            with connection:
                yield connection

    def attachment(self, name=None, fid=10, tree=21, polygon=False):
        table = "drzewa_umiar_ryz_zalaczniki" if polygon else "drzewa_zalaczniki"
        with self.database() as connection:
            connection.execute(f"INSERT INTO {table} VALUES(?,?,?)",
                               (fid, tree, name or self.name))

    def photo(self, name=None, suffix=".JPG", size=(60, 40)):
        path = self.images / ((name or self.name) + suffix)
        with Image.new("RGB", size, "green") as image:
            image.save(path, format="JPEG")
        return path

    def run_cli(self, apply=False):
        args = [str(self.gpkg), str(self.images), "--output-dir", str(self.output)]
        if apply:
            args.append("--apply")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = app.main(args)
        return result, out.getvalue(), err.getvalue()

    def snapshot(self):
        return {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [self.gpkg, *self.images.iterdir()]
        }

    def plan(self):
        connection = app.readonly_database(self.gpkg)
        try:
            return app.build_plan(connection, self.images)
        finally:
            connection.close()

    def test_success_preserves_originals_and_unrelated_data(self):
        self.attachment()
        source = self.photo()
        unrelated = self.photo("21b")
        before = self.snapshot()
        result, _, errors = self.run_cli(apply=True)
        self.assertEqual(result, 0, errors)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.output / "images" / (self.target + ".jpg")).read_bytes(),
                         source.read_bytes())
        with self.database(self.output / self.gpkg.name) as copy:
            self.assertEqual(copy.execute(
                "SELECT fid,fid_drzewo,nazwa_pliku FROM drzewa_zalaczniki"
            ).fetchall(), [(10, 21, self.target)])
            self.assertEqual(copy.execute(
                f"SELECT * FROM {app.identifier(self.point)}"
            ).fetchall(), [(21, "D747994", 909690, b"untouched geometry")])
            self.assertEqual(copy.execute("SELECT * FROM untouched").fetchall(),
                             [("unchanged",)])
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(copy.execute("PRAGMA application_id").fetchone(),
                             (1196444487,))
        report = json.loads((self.output / "report.json").read_text())
        self.assertEqual(report["unmatched_files"], [unrelated.name])
        self.assertEqual(report["conversions"][0]["tree_fid"], 21)
        self.assertFalse(any(self.root.glob(".*.staging-*")))
        self.assertFalse((self.root / ".results.lock").exists())

    def test_dry_run_is_readonly(self):
        self.attachment()
        self.photo()
        before = self.snapshot()
        result, text, errors = self.run_cli()
        self.assertEqual(result, 0, errors)
        self.assertIn(self.target + ".jpg", text)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.snapshot(), before)

    def test_missing_photo_blocks_all_output(self):
        self.attachment()
        self.photo("21b")
        before = self.snapshot()
        result, text, errors = self.run_cli(apply=True)
        self.assertEqual(result, 1)
        self.assertIn("brak zdjecia", errors)
        self.assertIn("21b.JPG", text)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_candidates_are_rejected(self):
        self.attachment()
        self.photo()
        self.photo(suffix=".jpeg")
        self.assertIn("wiele plikow", "\n".join(self.plan().errors))

    def test_orphan_is_rejected(self):
        self.attachment(tree=999)
        self.assertIn("Brak drzewa", "\n".join(self.plan().errors))

    def test_invalid_names_are_rejected(self):
        for name in [
            "../D747994_2_20261016_Park", "D747994_0_20261016_Park",
            "D747994_2_20260230_Park", "D747994_2_20261016_Park.",
            "D123_2_20261016_Park", "D747994_2_20261016_Pa:rk",
            "D747994_2_20261016_Park.jpg",
        ]:
            with self.subTest(name=name):
                with self.assertRaises(app.NormalizationError):
                    app.target_name(name, 21, "D747994")

    def test_polygon_and_already_normalized(self):
        self.attachment(self.target, polygon=True)
        self.photo(self.target)
        result, _, errors = self.run_cli(apply=True)
        self.assertEqual(result, 0, errors)
        with self.database(self.output / self.gpkg.name) as copy:
            self.assertEqual(copy.execute(
                "SELECT nazwa_pliku FROM drzewa_umiar_ryz_zalaczniki"
            ).fetchone(), (self.target,))
        self.assertTrue((self.output / "images" / (self.target + ".jpg")).exists())

    def test_case_insensitive_collision_across_tables(self):
        self.attachment()
        other = self.name.replace("Park", "park")
        self.attachment(other, polygon=True)
        self.photo()
        self.photo(other)
        # On a case-insensitive filesystem the second photo can replace the
        # first; target collision detection must still block the package.
        self.assertIn("Kolizja", "\n".join(self.plan().errors))

    def test_shared_reference_writes_one_image(self):
        self.attachment()
        self.attachment(polygon=True)
        self.photo()
        result, _, errors = self.run_cli(apply=True)
        self.assertEqual(result, 0, errors)
        self.assertEqual(len(list((self.output / "images").iterdir())), 1)
        report = json.loads((self.output / "report.json").read_text())
        self.assertEqual(len(report["conversions"]), 2)

    def test_existing_output_is_not_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        result, _, errors = self.run_cli(apply=True)
        self.assertEqual(result, 1)
        self.assertIn("juz istnieje", errors)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_output_inside_sources_is_rejected(self):
        self.output = self.images / "results"
        result, _, errors = self.run_cli(apply=True)
        self.assertEqual(result, 1)
        self.assertIn("danych wejsciowych", errors)

    def test_corrupt_image_cleans_staging(self):
        self.attachment()
        (self.images / (self.name + ".jpg")).write_bytes(b"not a jpeg")
        before = self.snapshot()
        result, _, _ = self.run_cli(apply=True)
        self.assertEqual(result, 1)
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.root.glob(".*.staging-*")))
        self.assertFalse((self.root / ".results.lock").exists())
        self.assertEqual(self.snapshot(), before)

    def test_database_failure_cleans_staging(self):
        self.attachment()
        self.photo()
        with self.database() as connection:
            connection.execute("""
                CREATE TRIGGER reject_update BEFORE UPDATE ON drzewa_zalaczniki
                BEGIN SELECT RAISE(ABORT,'update rejected'); END
            """)
        before = self.snapshot()
        result, _, errors = self.run_cli(apply=True)
        self.assertEqual(result, 1)
        self.assertIn("update rejected", errors)
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.root.glob(".*.staging-*")))
        self.assertEqual(self.snapshot(), before)

    def test_oversized_jpeg_orientation_and_size(self):
        source = self.images / "large.jpg"
        exif = Image.Exif()
        exif[274] = 6
        exif[36867] = "2026:09:16 11:54:36"
        with Image.new("RGB", (120, 80), "blue") as image:
            image.save(source, format="JPEG", quality=98, exif=exif)
        with source.open("ab") as handle:
            handle.write(b"\0" * app.MAX_BYTES)
        before = source.read_bytes()
        destination = self.root / "small.jpg"
        report = app.process_image(source, destination)
        self.assertLessEqual(destination.stat().st_size, app.MAX_BYTES)
        self.assertEqual(report["action"], "recompressed")
        with Image.open(destination) as result:
            self.assertEqual(result.size, (80, 120))
            self.assertNotEqual(result.getexif().get(274), 6)
            self.assertEqual(result.getexif().get(36867), "2026:09:16 11:54:36")
        self.assertEqual(source.read_bytes(), before)

    def test_dimension_reduction_when_quality_is_insufficient(self):
        source = self.images / "noisy.jpg"
        with Image.effect_noise((128, 80), 100).convert("RGB") as image:
            image.save(source, format="JPEG", quality=100)
        destination = self.root / "reduced.jpg"
        with patch.object(app, "MAX_BYTES", 800):
            report = app.process_image(source, destination)
        self.assertLessEqual(destination.stat().st_size, 800)
        width, height = report["output_dimensions"]
        self.assertLess(width, 128)
        self.assertAlmostEqual(width / height, 128 / 80, delta=0.08)

    def test_non_jpeg_with_jpg_extension_is_rejected(self):
        source = self.images / "false.jpg"
        with Image.new("RGB", (20, 20)) as image:
            image.save(source, format="PNG")
        with self.assertRaises(app.NormalizationError):
            app.process_image(source, self.root / "output.jpg")

    def test_ambiguous_parent_layer_is_rejected(self):
        self.attachment()
        with self.database() as connection:
            name = self.point + "_another"
            connection.execute("INSERT INTO gpkg_contents VALUES(?, 'features')", (name,))
            connection.execute("INSERT INTO gpkg_geometry_columns VALUES(?, 'POINT')",
                               (name,))
        self.assertIn("warstwy nadrzednej", "\n".join(self.plan().errors))

    def test_backup_includes_uncheckpointed_wal(self):
        writer = sqlite3.connect(self.gpkg)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO drzewa_zalaczniki VALUES(10,21,?)",
                           (self.name,))
            writer.commit()
            self.photo()
            result, _, errors = self.run_cli(apply=True)
            self.assertEqual(result, 0, errors)
            with self.database(self.output / self.gpkg.name) as copy:
                self.assertEqual(copy.execute(
                    "SELECT nazwa_pliku FROM drzewa_zalaczniki"
                ).fetchone(), (self.target,))
            self.assertEqual(writer.execute(
                "SELECT nazwa_pliku FROM drzewa_zalaczniki"
            ).fetchone(), (self.name,))
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()

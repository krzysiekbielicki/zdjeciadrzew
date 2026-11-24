# Przygotowanie zdjęć i pliku GeoPackage dla MoDrzew

Skrypt `normalize_modrzew.py` tworzy **kopię** pliku `.gpkg` oraz katalog zdjęć
z nazwami opartymi na FID drzewa. Aktualizuje odwołania do zdjęć w kopii bazy.
Oryginalne zdjęcia i GeoPackage pozostają bez zmian.

## 1. Wymagania

- Windows 11 albo Windows 10 w wersji 2004 lub nowszej, kompilacja co najmniej
  19041, dla opisanej metody instalacji WSL.
- Uprawnienia administratora do instalacji WSL oraz dostęp do internetu.
- Ubuntu w WSL, Python **3.10 lub nowszy** oraz biblioteka Pillow.
- Pliki `normalize_modrzew.py` i `requirements.txt` z tego samego pakietu.
- GeoPackage z tabelami załączników MoDrzew oraz komplet powiązanych zdjęć.

Nie trzeba instalować QGIS ani narzędzia `sqlite3`. Obsługa SQLite jest częścią
Pythona. Komendy z oznaczeniem **PowerShell** uruchamiaj w Windows;
komendy **Ubuntu** — w terminalu Ubuntu.

## 2. Instalacja WSL i Ubuntu

W menu Start wyszukaj **PowerShell**, kliknij prawym przyciskiem myszy
i wybierz **Uruchom jako administrator**.

**PowerShell:**

```powershell
wsl --install -d Ubuntu
```

Uruchom ponownie komputer, jeżeli instalator tego wymaga. Następnie otwórz
**Ubuntu** z menu Start. Poczekaj na zakończenie pierwszej konfiguracji.
Utwórz nazwę użytkownika Linux i hasło. Hasło może być inne niż hasło Windows;
podczas jego wpisywania terminal nie wyświetla znaków — to normalne.

Sprawdź instalację:

**PowerShell:**

```powershell
wsl --list --verbose
```

Nowa instalacja powinna korzystać z WSL 2. Ubuntu można też otworzyć poleceniem:

```powershell
wsl -d Ubuntu
```

Jeśli instalacja zatrzymuje się na 0%, Microsoft zaleca alternatywne pobieranie:

```powershell
wsl --install --web-download -d Ubuntu
```

Dokumentacja Microsoft:
[instalacja WSL](https://learn.microsoft.com/pl-pl/windows/wsl/install)
i [rozwiązywanie problemów](https://learn.microsoft.com/pl-pl/windows/wsl/troubleshooting).
Na komputerze firmowym ograniczenia instalacji uzgodnij z administratorem.

## 3. Instalacja Pythona i zależności

**Ubuntu:**

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
python3 --version
```

Jeśli wersja Pythona jest starsza niż 3.10, użyj nowszej wersji Ubuntu.
Polecenie `sudo` może poprosić o hasło użytkownika Linux.

Utwórz osobne środowisko Pythona w katalogu domowym Linux:

```bash
python3 -m venv ~/.venvs/modrzew
source ~/.venvs/modrzew/bin/activate
```

Przykładowo umieść skrypt i `requirements.txt` w `C:\MoDrzew\narzedzie`.
W Ubuntu przejdź do tego katalogu i zainstaluj zależności:

```bash
cd "/mnt/c/MoDrzew/narzedzie"
python3 -m pip install -r requirements.txt
```

Nie instaluj Pillow poleceniem `sudo pip`. W każdym nowym terminalu przed
uruchomieniem skryptu ponownie aktywuj środowisko:

```bash
source ~/.venvs/modrzew/bin/activate
cd "/mnt/c/MoDrzew/narzedzie"
```

## 4. Przygotowanie plików i ścieżki Windows

Przykładowy układ katalogów w Windows:

```text
C:\MoDrzew\
  narzedzie\
    normalize_modrzew.py
    requirements.txt
  dane\
    park ceramiczna_f.gpkg
    zdjecia\
      D747994_1_20261016_ParkCeramiczna.JPG
      D747994_2_20261016_ParkCeramiczna.JPG
      ...pozostałe wymagane zdjęcia...
```

W Ubuntu dyski Windows są zwykle dostępne tak:

| Windows | Ubuntu / WSL |
| --- | --- |
| `C:\MoDrzew\dane` | `/mnt/c/MoDrzew/dane` |
| `D:\Inwentaryzacja\zdjecia` | `/mnt/d/Inwentaryzacja/zdjecia` |

W poleceniach używaj ścieżek WSL i otaczaj je cudzysłowami, zwłaszcza gdy
zawierają spacje. Zastąp przykładowe ścieżki własnymi.

Wszystkie wymagane zdjęcia umieść bezpośrednio w podanym katalogu.
Skrypt **nie przeszukuje podkatalogów**. Zamknij QGIS, MoDrzew i inne programy
zapisujące dane do źródłowego GeoPackage; nie zmieniaj zdjęć w trakcie pracy.

## 5. Podgląd zmian bez zapisu

**Ubuntu**, z katalogu zawierającego skrypt i po aktywowaniu środowiska:

```bash
python3 normalize_modrzew.py "/mnt/c/MoDrzew/dane/park ceramiczna_f.gpkg" "/mnt/c/MoDrzew/dane/zdjecia"
```

Dwa obowiązkowe argumenty to, w tej kolejności, **plik GeoPackage** i
**katalog zdjęć**. Bez `--apply` skrypt pokazuje plan, ale nie tworzy wyników.
Podgląd sprawdza nazwy i relacje w bazie; pełne odczytanie i przetworzenie
obrazów następuje podczas tworzenia wyników.

Przykładowa zmiana:

```text
D747994_2_20261016_ParkCeramiczna -> 21_2_20261016_ParkCeramiczna.jpg
```

Komunikaty o brakujących zdjęciach lub niejednoznacznych nazwach trzeba
rozwiązać przed utworzeniem pakietu. Błąd oznacza kod zakończenia `1`;
poprawny podgląd lub zapis kończy się kodem `0`.

## 6. Utworzenie wyników

Wybierz **nowy, nieistniejący katalog**, poza katalogiem zdjęć wejściowych.
Jego katalog nadrzędny musi już istnieć.

**Ubuntu:**

```bash
python3 normalize_modrzew.py "/mnt/c/MoDrzew/dane/park ceramiczna_f.gpkg" "/mnt/c/MoDrzew/dane/zdjecia" --apply --output-dir "/mnt/c/MoDrzew/wynik"
```

Wyniki:

```text
C:\MoDrzew\wynik\
  park ceramiczna_f.gpkg
  images\
    21_1_20261016_ParkCeramiczna.jpg
    21_2_20261016_ParkCeramiczna.jpg
    ...pozostałe powiązane zdjęcia...
  report.json
```

Nazwa pliku GeoPackage pozostaje taka sama, ale plik znajduje się w nowym
katalogu. `report.json` zawiera stare i nowe nazwy, FID drzewa, rozmiary zdjęć,
informacje o kompresji i listę niepowiązanych plików.

Jeśli pominiesz `--output-dir`, wynik trafi do katalogu obok GeoPackage
o nazwie `<nazwa_bez_rozszerzenia>_normalized`.

**Istniejące wyniki nie są nadpisywane.** Do kolejnego uruchomienia wybierz
inną nazwę katalogu. Brak choćby jednego wymaganego zdjęcia, uszkodzony JPEG
lub błąd przetwarzania blokuje publikację całego pakietu.

## 7. Zasady zmiany nazw i kompresji

Skrypt obsługuje tabele `drzewa_zalaczniki` oraz
`drzewa_umiar_ryz_zalaczniki`, powiązane z odpowiednimi kodowanymi warstwami
punktowymi i poligonowymi.

Przykład dla drzewa o `fid=21` i `numer_inw=D747994`:

```text
Zdjęcie wejściowe: D747994_2_20261016_ParkCeramiczna.JPG
Zdjęcie wynikowe:  21_2_20261016_ParkCeramiczna.jpg
nazwa_pliku:      21_2_20261016_ParkCeramiczna
```

FID pochodzi z **rekordu drzewa**, wskazanego przez `fid_drzewo`, a nie
z numeru rekordu załącznika ani pola `id`. Numer zdjęcia, data i opis pozostają
niezmienione, również gdy opis zawiera podkreślenia. Nazwy już oparte na FID
są akceptowane.

W GeoPackage nazwy pozostają **bez rozszerzenia**, zgodnie z przyjętą
konwencją wejściowego pliku. Wymagany wzorzec referencji to
`identyfikator_numerZdjecia_RRRRMMDD_opis`.

Dopasowanie wymaga identycznej nazwy zdjęcia bez rozszerzenia, łącznie
z wielkością liter. Rozszerzenia źródłowe `.jpg`, `.JPG` i `.jpeg`
(również inne warianty wielkości liter) są akceptowane; plik musi faktycznie
być JPEG. Dwa pliki o tej samej nazwie bazowej są niejednoznaczne.

Zdjęcia do **5 000 000 bajtów** są kopiowane bez zmiany zawartości. Większe
są ponownie zapisywane jako JPEG ze stopniowo zmniejszaną jakością, a w razie
potrzeby także wymiarami, z zachowaniem proporcji. Kompresja jest stratna.
Przy ponownym zapisie skrypt uwzględnia orientację EXIF; data EXIF nie służy
do zmiany nazwy. Nazwa wynikowego rozszerzenia zawsze brzmi `.jpg`.

## 8. Typowe problemy

| Problem | Co zrobić |
| --- | --- |
| Nie można otworzyć `normalize_modrzew.py` | Przejdź przez `cd` do katalogu zawierającego skrypt. |
| Brak `requirements.txt` | Umieść go obok skryptu i uruchom instalację z tego katalogu. |
| Brak Pillow | Aktywuj środowisko i wykonaj `python3 -m pip install -r requirements.txt`. |
| Nie istnieje plik lub katalog | Sprawdź mapowanie `C:\...` na `/mnt/c/...` i cudzysłowy. |
| Brakuje zdjęcia wskazanego w bazie | Dodaj właściwy plik z identyczną nazwą bazową. Nie twórz zastępczych zdjęć. |
| Wiele plików dla jednej nazwy | Ustal właściwy plik i przenieś zbędną kopię poza katalog wejściowy. |
| Kolizja nazw wynikowych | Sprawdź powiązania oraz opisy/numerację zdjęć; skrypt nie wybiera pliku za użytkownika. |
| Katalog wynikowy już istnieje | Podaj nowy katalog przez `--output-dir`. |
| Blokada `.nazwa.lock` | Upewnij się, że inne uruchomienie zakończyło pracę. Po przerwanym procesie usuń tylko jego konkretny plik blokady i pozostały katalog tymczasowy, po sprawdzeniu ich zawartości. |
| Uszkodzony lub nieobsługiwany obraz | Przywróć prawidłowy JPEG; sama zmiana rozszerzenia nie konwertuje obrazu. |

Plik `21b.JPG` nie zostanie automatycznie przypisany do drzewa 21.
Skrypt nie zgaduje znaczenia takich nazw. Zdjęcia bez odwołania w bazie
są zgłaszane i pozostają bez zmian; nie trafiają do wyników.

## 9. Ograniczenia i weryfikacja

Strona 12 PDF wskazuje FID w nazwie, natomiast strona 13 pokazuje numer
inwentaryzacyjny. Skrypt realizuje wybrany wariant **FID**; nie dowodzi to,
że numer inwentaryzacyjny był przyczyną odrzucenia zdjęć przez MoDrzew.
Rozbieżność daty w nazwie i EXIF nie jest automatycznie poprawiana.

Program wymaga niepustych, poprawnych referencji bez rozszerzenia oraz
jednoznacznego powiązania z obsługiwaną warstwą nadrzędną. Nie odtwarza
brakujących zdjęć ani dowolnych schematów nazewnictwa.

Publikacja używa katalogu tymczasowego i zmiany jego nazwy dopiero po
sprawdzeniu wyników. Nie jest to gwarancja odporności na utratę zasilania.
Nie twórz ani nie zmieniaj katalogu wynikowego podczas działania skryptu.

Testy automatyczne obejmują syntetyczne dane oraz pracę na macOS.
Uruchomienie pod Windows/WSL i rzeczywisty import w MoDrzew nie zostały
sprawdzone. Zachowaj oryginały i zweryfikuj otrzymany pakiet przed przekazaniem.

Testy programu można uruchomić ze środowiska zawierającego Pillow:

```bash
python3 -m unittest discover -s tests
```

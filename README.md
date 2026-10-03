# Ricoh LAN-Fax for Linux

Unofficial Linux path for **Ricoh LAN-Fax Generic** (tested against an IM 350F lab). Print from any application to a CUPS printer, pick recipients in a popup (phonebook, cover page, preview), and send a Windows-shaped RAW job to TCP **9100**.

This is not an official Ricoh product. Do not publish captures, phone numbers, or printer IPs.

## Features

- CUPS printer **Ricoh-LAN-Fax** with a number dialog (no extra app has to stay open)
- Cover page, preview, zoom, and address book (local **or** LDAP/vCard)
- **NovaMail**-compatible directory: LDAP `ldap://host:1389` / CardDAV `http://host:8765/addressbooks/novamail/`
- Ubuntu app-menu entry **Ricoh LAN-Fax** for device IP and address-book source
- Production install / `.deb` ships the printer path only — **no** Windows capture sink app

## Dependencies

On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-tk python3-pil python3-ldap3 ghostscript cups cups-client cups-bsd
```

| Package | Why |
| --- | --- |
| `python3` | Runtime (3.11+) |
| `python3-tk` | Fax popup and phonebook |
| `python3-pil` | Smooth preview scaling (optional but recommended) |
| `python3-ldap3` | LDAP client for NovaMail / standard directories |
| `ghostscript` | PDF/PS → MMR/G4 at 200 dpi, A4, 1728×2259 |
| `cups` / `cups-client` / `cups-bsd` | Printer queue, `lpadmin`, `lp` |

## Install (production)

### From a release `.deb` (recommended)

Do **not** double-click / `apt install` the `.deb` straight from `~/Downloads` — apt then warns about the sandbox and may fail oddly. Use the installer script (copies the package to `/tmp`, installs `python3-tk` and friends first, then installs the `.deb`):

```bash
# download both files from the GitHub Release into ~/Downloads, then:
chmod +x install-deb.sh
sudo ./install-deb.sh ~/Downloads/ricoh-lanfax_0.3.0_all.deb
```

Or, if `install-deb.sh` sits next to the `.deb` / finds it in `~/Downloads`:

```bash
sudo ./install-deb.sh
```

### From a git checkout

```bash
git clone https://github.com/AniGerm/Ricoh-LAN-Fax.git
cd Ricoh-LAN-Fax
sudo ./install-printer.sh
```

This installs the CUPS queue, backend, fax popup (including phonebook), and an Ubuntu menu entry. It does **not** install the lab sink app.

Then print to **Ricoh-LAN-Fax** from LibreOffice, Firefox, and similar. A dialog asks for fax numbers. Or open **Ricoh LAN-Fax** from the app menu to set the **device IP** (IM 350F, Raw 9100) and edit the phonebook without a print job:

```bash
ricoh-lanfax settings
```

Stuck jobs:

```bash
cancel -a Ricoh-LAN-Fax
```

Backend log: `/var/tmp/ricoh-lanfax/backend.log`

Remove:

```bash
sudo ./uninstall-printer.sh
# or: sudo apt remove ricoh-lanfax
```

## Address book and settings

Stored only on the local machine (not in this repository):

- `~/.config/ricoh-lanfax/config.json` — printer host/port, cover defaults
- `~/.config/ricoh-lanfax/phonebook.json` — contacts, recents, favorites, directory sources

Do not commit those files.

### Local vs NovaMail / LDAP

In **Ricoh LAN-Fax** (app menu) → **Adressbuch-Quelle…**, or in the phonebook → **Quelle…**:

1. **Lokales Adressbuch** — contacts only on this PC  
2. **Verzeichnis (LDAP / vCard / NovaMail)** — shared book from another host  

For [NovaMail](https://github.com/AniGerm/NovaMail) on the main PC (**Server / shared address book** mode):

1. Enter the NovaMail LAN IP and click **NovaMail-Standard**  
2. Paste the password shown in NovaMail (same as CardDAV)  
3. Keep **LDAP** enabled (port **1389**, base `ou=people,dc=novamail`, bind `cn=novamail,dc=novamail`)  
4. Optionally enable CardDAV/vCard (`http://<host>:8765/addressbooks/novamail/`)  
5. **Verbindung prüfen** → **Speichern**

Fax numbers prefer `facsimileTelephoneNumber` / `TEL;TYPE=FAX`, then voice numbers.

Example file: [phonebook.ldap.example.json](phonebook.ldap.example.json)

## Send from the command line

```bash
ricoh-lanfax send document.pdf --host <PRINTER_IP> --number 0123456789
# or: python3 -m ricoh_lanfax send …
```

`--dry-run` builds the job without connecting. `--dump path.raw` writes the RAW stream.

## Lab sink (developers only)

The Windows capture sink / lab GUI is **not** part of the production install. From a git checkout:

```bash
./start.sh --terminal   # terminal sink on Raw 9100
./start.sh              # lab GUI (close it while printing via CUPS)
```

Point a Windows LAN-Fax printer at **Raw 9100** on this computer. Files appear under `captures/` (gitignored). Inspect:

```bash
python3 -m ricoh_lanfax parse captures/job.raw
python3 -m ricoh_lanfax diff captures/a.raw captures/b.raw
```

## Build a `.deb`

```bash
./packaging/build-deb.sh
# → dist/ricoh-lanfax_<version>_all.deb
```

Tagged releases (`v*`) build the package in GitHub Actions and attach it to the GitHub Release.

## Tests

```bash
python3 -m unittest discover -s tests -q
```

## Protocol notes

Jobs match Win11 LAN-Fax Generic (PC-FAX): UEL + `@PJL PCFAXJOB` + `DRIVERKINDINFO=PCFAXGENERIC` + `ENTER LANGUAGE=RFAX`. Destination is RFAX `DEST_ADDRESS` (type 0, ASCII) plus checksum. Pages are raw MMR/G4, A4, 200 dpi, line count 2259. No `END_PHYSICAL_PAGE` (same as current LTSC captures).

## License

MIT for the Linux code in this repository. Do not copy Ricoh’s Windows driver, INF/LUA, or installer into the repo.

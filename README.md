# Ricoh LAN-Fax for Linux

Unofficial Linux path for **Ricoh LAN-Fax Generic** (tested against an IM 350F lab). Print from any application to a CUPS printer, pick recipients in a popup (phonebook, cover page, preview), and send a Windows-shaped RAW job to TCP **9100**.

This is not an official Ricoh product. Do not publish captures, phone numbers, or printer IPs.

## Features

- CUPS printer **Ricoh-LAN-Fax** with a number dialog (no extra app has to stay open)
- Cover page, preview, zoom, and a local address book (favorites, recents, search)
- Ubuntu app-menu entry **Ricoh LAN-Fax** to set the device IP/port and manage the phonebook without printing
- Production install / `.deb` ships the printer path only — **no** Windows capture sink app

## Dependencies

On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-tk python3-pil ghostscript cups cups-client cups-bsd
```

| Package | Why |
| --- | --- |
| `python3` | Runtime (3.11+) |
| `python3-tk` | Fax popup and phonebook |
| `python3-pil` | Smooth preview scaling (optional but recommended) |
| `ghostscript` | PDF/PS → MMR/G4 at 200 dpi, A4, 1728×2259 |
| `cups` / `cups-client` / `cups-bsd` | Printer queue, `lpadmin`, `lp` |

## Install (production)

### From a release `.deb`

```bash
sudo apt install ./ricoh-lanfax_0.2.1_all.deb
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
- `~/.config/ricoh-lanfax/phonebook.json` — contacts, recents, favorites

Do not commit those files.

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

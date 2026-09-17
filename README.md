# Ricoh LAN-Fax for Linux

Unofficial Linux path for **Ricoh LAN-Fax Generic** (tested against an IM 350F lab). Print from any application to a CUPS printer, pick recipients in a popup, and send a Windows-shaped RAW job to TCP **9100**.

This is not an official Ricoh product. Do not publish captures, phone numbers, or printer IPs.

## Features

- CUPS printer **Ricoh-LAN-Fax** with a number dialog (no extra app has to stay open)
- Cover page, preview, zoom, and a local address book (LDAP/vCard hooks later)
- Optional capture sink so a Windows VM can fax into this machine for protocol checks

## Dependencies

On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-tk python3-pil ghostscript cups cups-client cups-bsd
```

| Package | Why |
| --- | --- |
| `python3` | Runtime (3.11+) |
| `python3-tk` | Popup and lab GUI |
| `python3-pil` | Smooth preview scaling (optional but recommended) |
| `ghostscript` | PDF/PS → MMR/G4 at 200 dpi, A4, 1728×2259 |
| `cups` / `cups-client` / `cups-bsd` | Printer queue, `lpadmin`, `lp` |

No PyPI packages are required. Python’s standard library is enough for send/sink.

## Install the printer

```bash
git clone https://github.com/AniGerm/Ricoh-LAN-Fax.git
cd Ricoh-LAN-Fax
sudo ./install-printer.sh
```

Then print to **Ricoh-LAN-Fax** from LibreOffice, Firefox, and similar. A dialog asks for fax numbers. Set the **device IP** (IM 350F, Raw 9100) with the gear icon.

Stuck jobs:

```bash
cancel -a Ricoh-LAN-Fax
```

Backend log: `/var/tmp/ricoh-lanfax/backend.log`

After code updates, run `sudo ./install-printer.sh` again (or keep using the checkout if the backend still finds `./ricoh_lanfax` next to `cups/`).

## Address book and settings

Stored only on the local machine (not in this repository):

- `~/.config/ricoh-lanfax/config.json` — printer host/port, cover defaults
- `~/.config/ricoh-lanfax/phonebook.json` — contacts, recents, favorites

Do not commit those files.

## Send from the command line

```bash
python3 -m ricoh_lanfax send document.pdf --host <PRINTER_IP> --number 0123456789
```

`--dry-run` builds the job without connecting. `--dump path.raw` writes the RAW stream.

## Lab sink (optional)

Capture Windows LAN-Fax Generic jobs on this host:

```bash
./start.sh --terminal
```

Point the Windows printer at **Raw 9100** on this computer. Files appear under `captures/` (gitignored). Inspect:

```bash
python3 -m ricoh_lanfax parse captures/job.raw
python3 -m ricoh_lanfax diff captures/a.raw captures/b.raw
```

GUI lab window (close it while printing via CUPS so it does not steal the popup):

```bash
./start.sh
```

## Tests

```bash
python3 -m unittest discover -s tests -q
```

## Protocol notes

Jobs match Win11 LAN-Fax Generic (PC-FAX): UEL + `@PJL PCFAXJOB` + `DRIVERKINDINFO=PCFAXGENERIC` + `ENTER LANGUAGE=RFAX`. Destination is RFAX `DEST_ADDRESS` (type 0, ASCII) plus checksum. Pages are raw MMR/G4, A4, 200 dpi, line count 2259. No `END_PHYSICAL_PAGE` (same as current LTSC captures).

## License

MIT for the Linux code in this repository. Do not copy Ricoh’s Windows driver, INF/LUA, or installer into the repo.

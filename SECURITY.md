# Security Policy

## Supported versions

- `0.1.x`: supported

## Reporting a vulnerability

If this repository is hosted on GitHub, please use **GitHub Security Advisories / private vulnerability reporting** when available.

If private reporting is not configured yet, do **not** publish sensitive details, recovered passwords, protected files, or exploit proof-of-concept material in a public issue. Instead, open a minimal issue asking the maintainers to enable a private reporting path.

## What to include in a report

Please include:

- affected version or commit
- deployment mode (`local` or `kali_mcp`)
- reproduction steps
- expected result
- actual result
- logs or traces with secrets removed
- whether the issue affects confidentiality, integrity, availability, or isolation boundaries

## Do not submit real protected content

Do **not** attach:

- real customer documents
- real recovered passwords
- production wordlists
- real secrets from `.env`

Use minimal synthetic fixtures or sanitized reproductions instead.

## Security boundaries

- The default web service binds to `127.0.0.1` unless configured otherwise.
- The worker runs as **UID 10001**.
- The default local worker runs with **no exposed ports** and `network_mode: none`.
- Compose applies `cap_drop: ALL`, `no-new-privileges:true`, and a read-only root filesystem.
- John runs with an isolated **per-job `HOME`** and restrictive temporary permissions.
- Job status and report outputs are designed to avoid password disclosure.
- Password reveal is **POST-only** and returned with `Cache-Control: no-store`.

## Known limitations

- This project performs password-recovery attempts and decryption only for operator-authorized workflows.
- No default `rockyou.txt` is bundled.
- The optional `kali_mcp` backend expands the trust boundary to an external compatible service and should be operated with care.
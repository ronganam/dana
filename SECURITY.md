# Security

Dana holds people's mail, messages, calls, home controls and bank logins, so security reports are welcome and taken seriously.

**Please don't open a public issue for a vulnerability.** Report it privately through GitHub: the repository's **Security** tab → **Report a vulnerability**. Include what you found, how to reproduce it, and what it lets an attacker do. You'll get an answer within a week.

What is in scope: the server (`server/`), the bank worker, the Android apps and the Raycast extension — e.g. one user reaching another user's data or tools, getting past authentication, making Dana act without the approval it should ask for, or leaking credentials. The security model is described in [`server/docs/access.md`](server/docs/access.md) ("Security rules").

Running Dana yourself: keep `API_TOKEN` and `CREDENTIALS_KEY` secret, put the server behind HTTPS, and don't expose it to the internet without a token.

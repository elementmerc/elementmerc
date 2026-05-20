# Profile README — design review

The **Terminal** direction was chosen. There are now **two terminal variants** to compare — same brand (red / grey / white / near-black), different cold-open. Open each and watch the header animate, then pick one.

---

## A · Terminal &nbsp;—&nbsp; the boot movie

Dead-channel TV static tunes into signal → a `mercury-os` splash → a ~30-line Linux boot that stalls on a real `systemd` start-job → hard cut to a **tty3 login** (`guest`, mistyped + corrected) → the session types itself out, each command followed by an ASCII `[====]` progress bar.

<img src="assets/terminal.svg" width="100%" alt="Terminal — boot movie" />

**→ [Open the full design](previews/02-terminal.md)**

---

## B · Terminal · Midnight Protocol &nbsp;—&nbsp; the operator login

Modelled on the login sequence of the hacking game **Midnight Protocol**: power-on → a keyboard-only `MERCURYOS` secure terminal → deliberate operator login (`operator id` + `passphrase`, pseudo-typed) → access granted → the **home screen**. Slower, ~20s, more atmospheric.

<img src="assets/terminal-midnight.svg" width="100%" alt="Terminal — Midnight Protocol operator login" />

**→ [Open the full design](previews/04-terminal-midnight.md)**

---

<details>
<summary>The three original concepts (where this started)</summary>

<br/>

- **[Mercury OS](previews/01-mercury-os.md)** — security-console HUD
- **[The Masthead](previews/03-masthead.md)** — editorial field-journal

</details>

---

<sub>Branch: `redesign` · nothing is live on the profile yet — `main` is untouched until a variant is chosen and the auto-updating automation is wired in.</sub>

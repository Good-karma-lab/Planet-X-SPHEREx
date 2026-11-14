# 7. Submission to NASA Space Apps 2026 and the pitch

Checked on 2026-11-14 in a browser (the site renders client-side) and in the organisers' GitHub discussions. Sources:
PSG = https://www.spaceappschallenge.org/resources/project-submission-guide/ · JAG = https://www.spaceappschallenge.org/resources/judging-awards-guide/
· FAQ = https://www.spaceappschallenge.org/resources/participant-faqs/ · T&C = https://www.spaceappschallenge.org/legal/ (updated 2026-08-07)
· GH = https://github.com/nasa/spaceapps/discussions. The drafts live in [`docs/submission/`](../submission/README.md).

## 7.1 Dates

| What | When | Source |
|---|---|---|
| Hackathon | Sat 14 – Sun 15 Nov 2026 | FAQ |
| Submission opens / closes | Sat 14 Nov 9:00 / **Sun 15 Nov 23:59, local time of the team's Local Event** (Barcelona: CET) | PSG |
| Edits | allowed until the deadline; only the last save is reviewed | PSG |
| Global Nominees / Finalists / Winners | 3 Dec 2026 / 17 Dec 2026 / 26 Jan 2027 | JAG |

## 7.2 Requirements and our answer

| Requirement | Source | Our answer / file | Done in |
|---|---|---|---|
| Fields: Project Name, High-Level Summary, Link to Demo, Link to Final Project, Detailed Description, Use of Super Intelligence (SI), NASA Data, Space Agency Partner Data and Other References; checkboxes T&C and "Confirm All Work is Original" | PSG, JAG | `submission/project-page.md` | Sun 21:00 |
| Final project link public, no password or registration (test in a private window) | PSG | the deployed tool's URL; repository public if linked (the user's call) | Sun 18:00 |
| Uploads on the page: images only (≤ 10 MB); video and documents as links | PSG | — | Sun 21:00 |
| Demo: video ≤ 30 s or ≤ 7 slides incl. title; over the limit = not eligible for global judging | PSG, JAG | `submission/video-script.md`, `submission/slides.md` | Sun 21:00 |
| English (video in another language needs English subtitles) | PSG | English | — |
| Slides show team name, members and the challenge | PSG | slide 1 | Sun 21:00 |
| At least one NASA data source from the challenge's Resources (IRSA SPHEREx, IRSA tutorials, WiseView, Gaia via MAST) | PSG; GH #1994, #2049; challenge Resources tab | SPHEREx Level-2 (IRSA), see `submission/data-and-references.md` | — |
| Partner data encouraged (17 agencies incl. ESA, CSA, Spain); cite every source incl. code, text, images | PSG, T&C | ESA Gaia DR3; references list | — |
| SI (Super Intelligence) allowed, not for coming up with the idea; images/video made by SI carry a visible watermark; SI audio, code, data acknowledged in text and metadata; no NASA logos, flags or mission identifiers in SI content | PSG; GH #2012 | `submission/si-disclosure.md`; commit co-author lines | — |
| Licence: OSI or unrestricted; third-party licences listed; no export-controlled content (default Apache 2.0) | T&C | a LICENSE file (the user's choice of licence) | Sun 21:00 |
| No images, voice or likeness of anyone under 18 | PSG, T&C | screen recordings only | — |
| Team 1–6, all confirmed at the same event, listed on the Members tab; nobody added after the deadline | T&C, JAG | the user | — |
| **No work on the project before 9:00 local on 14 Nov** (incl. downloading data); research and team forming before are fine | GH #1981, #2009, #2011, #2057 | this repository starts on 14 Nov after 9:00 CET (first commit 04b3f54); prior research is cited as research | — |

## 7.3 Judging criteria (1–5 each) and what the system shows

| Criterion (JAG) | What judges should see |
|---|---|
| Impact | anyone opens a place and sees real SPHEREx sky change within seconds; leads anyone can share by a link |
| Creativity | the blink comparator done honestly (pendulum of clean stacks), colour = spectrum, the distance slider for bodies beyond Neptune |
| Validity | measured agreement with Gaia, 2MASS/WISE and JPL; chance controls; leads not discoveries |
| Relevance | the challenge statement (display SPHEREx images, change over time, moving objects) met point by point; NASA data integrated |
| Presentation | the 30-s video or 7 slides, a tool that works from a private window |

Awards to aim at (JAG): Best Use of Data, Best Use of Science, Best Use of Technology.

## 7.4 Pitch materials

- 30-second video: script and shot list in `submission/video-script.md` (record when the deployed tool shows each scene; never a scene
  it cannot reproduce).
- 7 slides: outline in `submission/slides.md`.

## 7.5 Global Offers (participant resources)

Claimed in the Space Apps dashboard (avatar → Offers → Claim Offer; GH #2035, #2058); the details page needs a login (not read):
Google Cloud (incl. Firebase, Maps API), Microsoft (Azure, Planetary Computer, Copilot, GitHub Copilot, Teams), GoDaddy domain + 1 year
of hosting (threads of 14 Nov report a Porkbun `spaceapps.earth` domain offer failing: GH #2104, #2116, #2121), Meteomatics, Miro. Some
ask for a card for verification. Amounts and deadlines: **unverified**.

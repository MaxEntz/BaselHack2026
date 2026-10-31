# Sésame: pitch deck

Open [`sesame-pitch-v6.html`](sesame-pitch-v6.html) in a browser. It is one self-contained file that works offline, apart from the demo recording beside it. Keys: arrows to move, `F` for fullscreen, `N` for the speaker notes, Cmd+P for a PDF.

## Slides

| # | Title | Content |
| --- | --- | --- |
| 1 | Create your AI from your site. | Logo, event and team |
| 2 | The first visitor is no longer a person. | Timeline: 2000s the website, 2010s the platforms, now agents over HTTP, 2035s agent protocols |
| 3 | We rethink network protocols for agents talking to agents. | Random one-way jets from AI agents to a restaurant, a hotel and a shop |
| 4 | Generate your agent with Sésame | A site goes through Sésame, which generates its agent and a control site |
| 5 | A new way of consuming the internet. | The guest with an AI agent, the owner on Sésame |
| 6 | Live demo | Full-screen frame for the recording |
| 7 | This future has already started. | Chips that build a model into the silicon, then four next steps |
| 8 | Thank you. Questions? | Logo |

## Demo recording

[`sesame-demo.mp4`](sesame-demo.mp4) sits next to the deck: 45 seconds, 2880×1620, no sound. Slide 6 plays it on arrival, and a click or space pauses it, so use the right arrow to leave that slide. To show another recording, replace the file, or drop one on the frame of slide 6, in which case it is gone after a reload.

The deck rounds the video's corners in its CSS, and the file itself is square. Played outside the deck, it shows small black corners at the bottom, from the window it was recorded in.

The recording shows the v10 mockups, not `code/web`: the merchant's chat and the management site run from a fixed script, and #127 tracks building them. Its public site is La Graine Verte restyled, while `code/sites` still has the green one (#148). Its chat with Claude is a replay and says so on screen: the three times it offers came from the real connector on 31 October 2026, but the window is not Claude.ai.

## Speaker notes

Each note opens with the team's script in French, followed by the English version. The French is an exception to the English-only rule of AGENTS.md §12, kept at the team's request.

## Where the slides go beyond the code

- **Slide 2:** "2035s" is the wording the team chose. The standard form for a decade would be "2030s".
- **Slide 3:** it draws the model. Today Claude goes through the gateway over HTTPS, the other agents are not connected, and only restaurants exist.
- **Slide 4:** the control site is being built ([ADR-0015](../../adr/0015-give-merchants-a-management-site-next-to-the-chat.md), #127): some of its pages exist, and a build doesn't give it the merchant's look yet.
- **Slide 7:** the cost claim comes from [Taalas](https://taalas.com/the-path-to-ubiquitous-ai/) alone, and the slide says so.

## Transitions

Slides crossfade through the View Transitions API. The cover's logo travels to the footer, the seed travels from slide 4 to slide 5, and one step forward replays a slide's build. Going back or jumping skips the build. With reduced motion or without the API, slides change instantly. Checked in Chrome only.

## Assets

- **Agent logos:** Claude, Codex and Gemini, from [`@lobehub/icons-static-svg`](https://www.npmjs.com/package/@lobehub/icons-static-svg) (MIT). The marks belong to their owners and are shown as examples.
- **Icons:** Material Icons (Apache-2.0).
- **Type:** Source Sans 3 (SIL OFL), embedded and subset.
- **Wordmark:** traced from a screenshot of the logo. Replace it with the vector file once that exists.

## Earlier directions

These keep the text and slide order of the first draft of the pitch, and are not maintained:

- [`sesame-pitch-neutral.html`](sesame-pitch-neutral.html): black and white, with product mockups as the only colour.
- [`sesame-pitch-rust.html`](sesame-pitch-rust.html): colours measured on Rust's interface.

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

The recording predates the merchant's AI agent (#244), so it shows the booking flow as it was before: re-record it if time allows, or play it as the flow's backup, not as the agent. It also shows the v10 mockups, not `code/web`: the merchant's chat and the management site run from a fixed script there, and `code/web` was built from them afterwards (#141 for Studio, #137 to #147 for the management site). Its public site is La Graine Verte restyled, which `code/sites` now has too (#148). Its chat with Claude is a replay and says so on screen: the three times it offers came from the real connector on 31 October 2026, but the window is not Claude.ai.

## Speaker notes

Each note opens with the team's script in French, followed by the English version. The French is an exception to the English-only rule of AGENTS.md §12, kept at the team's request. They describe the flow on the merchant's AI agent: slide 5's guest talks to the restaurant's agent, which books only after a yes, and slide 6's steps are the [demo runbook](../../runbooks/demo.md)'s, in short.

## Where the slides go beyond the code

- **Slide 2:** "2035s" is the wording the team chose. The standard form for a decade would be "2030s".
- **Slide 3:** the agent talking to an agent is real: Claude goes through the gateway over HTTPS, the gateway speaks BATP (`TALK`) to the merchant's AI agent, and a guest agent of ours speaks BATP itself (#252). That agent is the only one that books, and its code runs a booking only on the guest's confirm, after the yes: a model reading the guest's text can propose, never write (#247, #254). The rest it draws as the model: outside agents can't connect yet, since port 4710 stays closed (#256), and only restaurants exist.
- **Slide 4:** the control site is the management site ([ADR-0015](../../adr/0015-give-merchants-a-management-site-next-to-the-chat.md), #127), in the merchant's own look, read from its homepage (#149, #150).
- **Slide 7:** the cost claim comes from [Taalas](https://taalas.com/the-path-to-ubiquitous-ai/) alone, and the slide says so.

## Transitions

Slides crossfade through the View Transitions API. The cover's logo travels to the footer, the seed travels from slide 4 to slide 5, and one step forward replays a slide's build. Going back or jumping skips the build. With reduced motion or without the API, slides change instantly. Checked in Chrome only.

## Assets

- **Agent logos:** Claude, Codex and Gemini, from [`@lobehub/icons-static-svg`](https://www.npmjs.com/package/@lobehub/icons-static-svg) (MIT). The marks belong to their owners and are shown as examples.
- **Icons:** Material Icons (Apache-2.0).
- **Type:** Source Sans 3 (SIL OFL), embedded and subset.
- **Wordmark:** traced from a screenshot of the logo. Replace it with the vector file once that exists.

# Sésame: POC v10

Status: the mockups the Sésame site is built from (#127, [ADR-0015](../../adr/0015-give-merchants-a-management-site-next-to-the-chat.md)). `code/web` and the backend are built from them, and the code wins over the mockups on booking rules.

## Management site

Open [`sesame-management.html`](sesame-management.html) in a browser. It is the current direction: one file that works offline, with both fonts embedded. It has the structure of a dashboard the user shared (sidebar, overview, assistant on the right) in the look of the page below. Add `#reservations`, `#guests`, `#hours`, `#floor`, `#rules`, `#agent`, `#settings` or `#assistant` to open a page directly.

- **Home:** guests over 30 days, tonight's bookings, the next 7 days, where bookings come from, the room tonight and recent activity.
- **Reservations, Guests, Opening hours, Floor plan, Booking rules, Settings:** bookings can be added, moved and cancelled, times blocked, and rules changed, with the rules of AGENTS.md §6.
- **Agent:** the agent site has no interface, so this page shows its address, a pause switch, and what it did today with the raw frames.
- **Assistant:** a fixed script over the same data, so "Block 13:00 on Sunday" really blocks it. No model runs.

The 7 days of bookings follow the rules of §6. The 30 days of history behind the chart are generated numbers. The clock is fixed at 19:40 on Saturday 31 October, and six seconds after opening, a demo booking arrives through the agent. Nothing leaves the page and nothing is saved.

## The look: built like a restaurant's website

Open [`la-graine-verte.html`](la-graine-verte.html) in a browser. It is one long page that sets the look used above, and also works offline.

A merchant's management site should look like their restaurant's own website, because dashboards read as AI-made. So this page is one long site with big soft italic titles, organic shapes and curved bands, and each section does a management job: tonight's next arrivals, the room at a chosen time, the bookings, what the agent did today, and the settings. The tables on the floor plan take the same organic shapes.

The layout, measurements and colours are adapted from a real restaurant's website, which the user chose as the reference. Its photos, logo, drawings, text and paid typeface are not used. Karla is the reference's body face, Fraunces replaces its display face, and the drawings are our own. La Graine Verte is the fictional restaurant from `code/tests/fixtures`; its demo site in `code/sites` has had this look since #148.

The data is generated with the rules of §6 and covers 7 days from Saturday 31 October, with the clock fixed at 19:40. Six seconds after opening, a demo booking arrives through Claude. Nothing leaves the page and nothing is saved.

## The public site in that look

Open [`la-graine-verte-public.html`](la-graine-verte-public.html) in a browser. It is the restaurant's public website from the demo video, in the look above, and also works offline. Its prices, hours, address, phone and group size are those of `code/sites/la-graine-verte`, in French, with the notice that the restaurant is fictional. It adds a menu bar and one line under "À la carte". #148 rebuilt the demo site from it, because a real build reads the public site.

## The conversation

Open [`sesame-studio.html`](sesame-studio.html) in a browser. It opens on the conversation. A merchant gives their website address and a few details, and the chat builds two sites from them. Add `#le-petit-zinc`, `#la-graine-verte` or `#maison-albane` to the address to open a restaurant that is already built.

## What the conversation builds

- **Agent site:** it has no interface. It is the address `batp://<host>/<slug>`, the frames an agent exchanges with it (`documentation/PROTOCOL.md`), and the tool descriptions Claude reads through the MCP bridge. The screen only shows that traffic, so a change to a rule shows up in the frames.
- **Management site:** the bookings, the floor and the rules, in the colours, type and vocabulary of the merchant's public site. It changes with the hour: the day ahead in the morning, the list before service, the next arrival in large type during service, and a recap after.
- **Public site:** left as it is. The page shows it to prove where each detail came from.

## What is real and what is simulated

| Real | Simulated |
| --- | --- |
| The text, hours and colours of the three restaurants, from `code/sites` and `code/tests/fixtures` | The answers: a fixed script replies, and no model runs |
| Bookable times, table choice and last seating (AGENTS.md §6), reimplemented in the page | The crawl: a static page can't read other sites, so another address gets defaults and says so |
| The shape of the BATP frames, intents and status codes (`PROTOCOL.md`) | The network: no frame leaves the page, and nothing is saved |
| Tool descriptions, from `templates/restaurant.json` and `generated_tools` | The bookings shown before publishing, marked as samples |
| Default floor plan, from `default_tables` | The management address, `<slug>.sesame.example`, which is invented (the real one is `/manage/<slug>`) |

## Still open

- **Port 4710:** the BATP node only listens on loopback. A public `batp://` address needs TLS and a firewall rule on the shared server, so ask first.

## Logo and colour

In the mockups, the wordmark is rebuilt from a screenshot of the logo, in Gloock with a hand-traced seed. The site uses the real one, from `assets/logo/`.

## Pitch decks

The BaselHack pitch. Each deck is one self-contained file that works offline. Keys: arrows or space to move, `F` for fullscreen, `N` for the notes, Cmd+P for a PDF.

- [`sesame-pitch-v6.html`](sesame-pitch-v6.html): the current deck. It starts from the text and notes of `Sesame_BaselHack_Pitch_v6.pptx`, in the layout of the neutral deck with the logo's orange for surfaces and rules. Slides 3 to 5 use pictograms and sell the idea without technical detail, and the text says "AI agent" rather than naming a product. Slide 2 goes from today's agent traffic over HTTP to protocols built for agents, slide 3 is the protocol, slide 4 shows Sésame generating, from a site, its agent and a control site, which so far exists only as the mockups above (building an agent by hand is only said aloud), and slide 5 is why: a guest with an agent and an owner who gets one. Slide 3 animates one-way jets, at random, from the Claude, Codex and Gemini logos (`@lobehub/icons-static-svg`) to a restaurant, a hotel and a shop (Material Icons, Apache-2.0); print and reduced motion show three still lines. It draws the model, not today's deployment: Claude goes through the gateway, the other agents are not connected, and only restaurants exist. Slide 6 is a full-screen 16:9 frame for the demo recording, `sesame-demo.mp4` next to the deck. Another one can be dropped on the frame. It plays on arrival, and a click or space pauses it. The video is the one file that stays outside the deck. Slides crossfade through the View Transitions API, the cover's logo travels to the footer, the seed travels between slides 4 and 5, and one step forward replays a slide's build. Going back, jumping, reduced motion and browsers without the API skip the build.

## Limits

`sesame-studio.html` loads Google Fonts. Offline it still opens, with fallback fonts. Browser QA ran in Chromium only, at 1440 px and 390 px, in light and dark.

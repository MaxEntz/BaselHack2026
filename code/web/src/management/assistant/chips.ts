// The assistant panel's suggestions (#140): the mockup's pool, unasked ones first four, with
// real days and no time that has already passed. `now` is the platform's clock in the
// merchant's timezone (`MerchantOverview.now`), so the web never computes its own today.

const SUNDAY = 0;
const SHOWN = 4;

/** The chips to offer, in the mockup's order, without those already asked. */
export function chips(now: string, asked: ReadonlySet<string>): string[] {
  return pool(now)
    .filter((chip) => !asked.has(chip))
    .slice(0, SHOWN);
}

export function pool(now: string): string[] {
  const minutes = minutesOf(now);
  const tonight = minutes < toMinutes("20:00") ? "tonight" : "tomorrow";
  const lunch = sundayWord(now, "13:00");
  return [
    "How busy are we tomorrow?",
    `Table for 4 at 20:00 ${tonight}`,
    `Bookings for ${lunch} lunch`,
    `Block 13:00 ${lunch === "Sunday" ? "on Sunday" : lunch}`,
    "What are my opening hours?",
    "Is my agent live?",
    "Set max party size to 6",
  ];
}

/** The next Sunday whose `time` is still ahead: "Sunday", or "next Sunday" a week away. */
function sundayWord(now: string, time: string): string {
  const weekday = new Date(`${now.slice(0, 10)}T00:00:00Z`).getUTCDay();
  let ahead = (SUNDAY - weekday + 7) % 7;
  if (ahead === 0 && minutesOf(now) >= toMinutes(time)) ahead = 7;
  return ahead === 7 ? "next Sunday" : "Sunday";
}

function minutesOf(now: string): number {
  return toMinutes(now.slice(11, 16));
}

function toMinutes(time: string): number {
  const [hours, minutes] = time.split(":").map(Number);
  return hours * 60 + minutes;
}

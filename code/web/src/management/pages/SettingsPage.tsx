import { useCallback } from "react";
import type { MerchantSettings, PriceLevel, Tag } from "../../types.ts";
import { useClient, useLoad } from "../data.ts";
import { LeaveGuard } from "../LeaveGuard.tsx";
import { PAGES } from "../pages.ts";
import { Checkbox, Field, SaveBar } from "../ui/index.ts";
import { useSavedForm } from "../useSavedForm.tsx";
import { PageTitle } from "./PageTitle.tsx";

const PRICES: readonly PriceLevel[] = ["€", "€€", "€€€", "€€€€"];
const TAGS: readonly Tag[] = [
  "gluten-free",
  "vegetarian",
  "vegan",
  "halal",
  "kosher",
  "terrace",
  "kid-friendly",
];

type SettingsDraft = { name: string; city: string; price_level: PriceLevel; tags: Tag[] };

// Tags in the checkboxes' order, so that unticking and ticking one again leaves nothing to save.
const draftOf = (settings: MerchantSettings): SettingsDraft => ({
  name: settings.name,
  city: settings.city ?? "",
  price_level: settings.price_level,
  tags: TAGS.filter((tag) => settings.tags.includes(tag)),
});

export function SettingsPage({ slug, version }: { slug: string; version: number }) {
  const client = useClient();
  const load = useCallback((signal: AbortSignal) => client.settings(slug, signal), [client, slug]);
  const { data, error } = useLoad(load, version);

  return (
    <>
      <PageTitle title={PAGES.settings}>Sésame filled these in from your public website.</PageTitle>
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {data && <SettingsForm slug={slug} settings={data} />}
    </>
  );
}

function SettingsForm({ slug, settings }: { slug: string; settings: MerchantSettings }) {
  const client = useClient();
  const form = useSavedForm(draftOf(settings), async (draft) =>
    draftOf(
      await client.saveSettings(slug, {
        name: draft.name.trim(),
        city: draft.city.trim() || null,
        price_level: draft.price_level,
        tags: draft.tags,
      }),
    ),
  );
  const toggle = (tag: Tag, on: boolean) =>
    form.set({ ...form.value, tags: TAGS.filter((t) => (t === tag ? on : form.value.tags.includes(t))) });

  return (
    <form className="forms" noValidate onSubmit={form.submit}>
      <div className="fs">
        <h3>The restaurant</h3>
        <div className="two">
          <Field
            label="Name"
            value={form.value.name}
            onChange={(event) => form.set({ ...form.value, name: event.target.value })}
          />
          <Field
            label="City"
            value={form.value.city}
            onChange={(event) => form.set({ ...form.value, city: event.target.value })}
          />
          <Field
            label="Website"
            hint="Sésame only reads it."
            readOnly
            value={new URL(settings.website).host}
          />
          <Field label="Prices">
            <select
              value={form.value.price_level}
              onChange={(event) => form.set({ ...form.value, price_level: event.target.value as PriceLevel })}
            >
              {PRICES.map((price) => (
                <option key={price}>{price}</option>
              ))}
            </select>
          </Field>
        </div>
        <div className="field">
          What guests can look for
          <div className="checks">
            {TAGS.map((tag) => (
              <Checkbox
                key={tag}
                label={tag[0].toUpperCase() + tag.slice(1)}
                checked={form.value.tags.includes(tag)}
                onChange={(event) => toggle(tag, event.target.checked)}
              />
            ))}
          </div>
        </div>
      </div>
      <SaveBar dirty={form.dirty} error={form.error} onDiscard={form.discard} />
      <LeaveGuard dirty={form.dirty} />
    </form>
  );
}

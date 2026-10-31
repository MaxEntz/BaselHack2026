import { useCallback } from "react";
import { strings } from "../../strings.ts";
import type { GeneratedTool, Rules } from "../../types.ts";
import { useClient, useLoad } from "../data.ts";
import { LeaveGuard } from "../LeaveGuard.tsx";
import { PAGES } from "../pages.ts";
import { Field, SaveBar } from "../ui/index.ts";
import { useSavedForm } from "../useSavedForm.tsx";
import { PageTitle } from "./PageTitle.tsx";

type RulesDraft = { largest: string; bookingPage: string; policy: string };

const draftOf = (rules: Rules): RulesDraft => ({
  largest: String(rules.max_party_size),
  bookingPage: rules.fallback_url,
  policy: rules.cancellation_policy ?? "",
});

export function RulesPage({ slug, version }: { slug: string; version: number }) {
  const client = useClient();
  const load = useCallback((signal: AbortSignal) => client.rules(slug, signal), [client, slug]);
  const { data, error, update } = useLoad(load, version);

  return (
    <>
      <PageTitle title={PAGES.rules} />
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <RulesForm slug={slug} rules={data} onSaved={(rules) => update(() => rules)} />
          <Reads tools={data.tools} />
        </>
      )}
    </>
  );
}

function RulesForm({
  slug,
  rules,
  onSaved,
}: {
  slug: string;
  rules: Rules;
  onSaved: (rules: Rules) => void;
}) {
  const client = useClient();
  const form = useSavedForm(draftOf(rules), async (draft) => {
    const saved = await client.saveRules(slug, {
      // An empty or partial number is no group at all: the server answers group_too_small.
      max_party_size: Number.parseInt(draft.largest, 10) || 0,
      fallback_url: draft.bookingPage.trim(),
      cancellation_policy: draft.policy.trim() || null,
    });
    onSaved(saved);
    return draftOf(saved);
  });
  const change = (key: keyof RulesDraft, value: string) => form.set({ ...form.value, [key]: value });

  return (
    <form className="forms" noValidate onSubmit={form.submit}>
      <div className="fs">
        <h3>What guests can book</h3>
        <div className="two">
          <Field
            label="Largest group"
            hint="Larger groups go to your booking page."
            type="number"
            min={1}
            value={form.value.largest}
            onChange={(event) => change("largest", event.target.value)}
          />
          <Field
            label="How long a table is held"
            hint="The same for every booking."
            readOnly
            value={`${rules.slot_length_min} minutes`}
          />
        </div>
        <Field
          label="Your booking page"
          hint="Guests land here when the agent can't help."
          type="url"
          value={form.value.bookingPage}
          onChange={(event) => change("bookingPage", event.target.value)}
        />
        <Field label="Cancellation policy">
          <textarea
            rows={2}
            value={form.value.policy}
            onChange={(event) => change("policy", event.target.value)}
          />
        </Field>
      </div>
      <SaveBar dirty={form.dirty} error={form.error} onDiscard={form.discard} />
      <LeaveGuard dirty={form.dirty} />
    </form>
  );
}

/** The tool descriptions an AI agent reads on the merchant's endpoint, as last saved. */
function Reads({ tools }: { tools: GeneratedTool[] }) {
  return (
    <div className="fs reads">
      <h3>{strings.readsAboutYou}</h3>
      <ul>
        {tools.map((tool) => (
          <li key={tool.name}>
            <code>{tool.name}</code>
            <span>{tool.description}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

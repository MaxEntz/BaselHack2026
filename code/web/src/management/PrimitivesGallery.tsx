import { type FormEvent, type ReactNode, useState } from "react";
import {
  Blob,
  Box,
  Checkbox,
  Dialog,
  Doodle,
  Field,
  Icon,
  type IconName,
  Pill,
  SaveBar,
  Seed,
  Tag,
  ToastProvider,
  Under,
  useDraft,
  useToast,
} from "./ui/index.ts";

const ICON_NAMES: IconName[] = ["left", "right", "x", "search", "bell", "up", "expand", "shrink"];

// Every management primitive and its states (/?gallery), for comparison with
// documentation/design/poc-v10/sesame-management.html. `data-primitive` marks the element
// e2e/primitives.spec.ts compares with the mockup's.
export function PrimitivesGallery() {
  return (
    <div className="mg mg-gallery">
      <ToastProvider>
        <h1 className="dsp">
          Management <Under>primitives</Under>
        </h1>
        <Section title="Pill">
          <div className="pills">
            <Pill data-primitive="pill">Default</Pill>
            <Pill primary data-primitive="pill-pri">
              Primary
            </Pill>
            <Pill small data-primitive="pill-sm">
              Small
            </Pill>
            <Pill small primary>
              Small primary
            </Pill>
            <Pill aria-pressed="true">Pressed</Pill>
            <Pill disabled>Disabled</Pill>
          </div>
        </Section>
        <Section title="Tag">
          <div className="pills">
            <Tag data-primitive="tag">vegan</Tag>
            <Tag>gluten-free</Tag>
          </div>
        </Section>
        <Section title="Box">
          <div className="mg-gallery-grid">
            <Box tone="olive" data-primitive="box-olive">
              <h2 className="dsp">Olive</h2>
              <p>Leather texture.</p>
              <Pill small>On dark</Pill>
            </Box>
            <Box tone="brick" data-primitive="box-brick">
              <h2 className="dsp">Brick</h2>
              <p>No leather.</p>
              <Pill small aria-pressed="true">
                Pressed
              </Pill>
            </Box>
            <Box tone="cream" data-primitive="box-cream">
              <h2 className="dsp">Cream</h2>
            </Box>
            <Box data-primitive="box-plain">
              <h2 className="dsp">Plain</h2>
            </Box>
          </div>
        </Section>
        <Section title="Blob">
          <div className="pills">
            {[0, 1, 2, 3, 4].map((index) => (
              <span key={index} className="mg-gallery-blob" style={index % 2 ? { ["--fill" as string]: "var(--brick)" } : undefined}>
                <Blob index={index} />
                <b className="dsp">{19 + index}:00</b>
              </span>
            ))}
          </div>
        </Section>
        <Section title="Under, Doodle, Seed, Icon">
          <div className="pills" style={{ alignItems: "center", fontSize: 28 }}>
            <span className="dsp">
              <Under>Tonight</Under>
            </span>
            <Doodle className="mg-gallery-doodle" />
            <Seed />
            {ICON_NAMES.map((name) => (
              <span key={name} title={name} data-primitive={`icon-${name}`}>
                <Icon name={name} />
              </span>
            ))}
          </div>
        </Section>
        <Section title="Field">
          <div className="mg-gallery-grid">
            <Field label="Restaurant name" defaultValue="La Graine Verte" data-primitive="field" />
            <Field label="Largest group" hint="Bigger groups get your website's link." type="number" defaultValue={4} />
            <Field label="Read only" defaultValue="la-graine-verte" readOnly />
            <div className="checks">
              <Checkbox label="Vegan" defaultChecked data-primitive="checkbox" />
              <Checkbox label="Terrace" />
            </div>
          </div>
        </Section>
        <Section title="Dialog, Toast">
          <DialogAndToast />
        </Section>
        <Section title="SaveBar">
          <SaveBarDemo />
        </Section>
      </ToastProvider>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mg-gallery-section">
      <h2 className="dsp">{title}</h2>
      {children}
    </section>
  );
}

function DialogAndToast() {
  const [open, setOpen] = useState(false);
  const toast = useToast();
  return (
    <div className="pills">
      <Pill onClick={() => setOpen(true)}>Open a dialog</Pill>
      <Pill onClick={() => toast("20:00 is blocked.")}>Toast</Pill>
      <Pill
        onClick={() =>
          toast(
            <>
              <b>New booking through your agent</b>Ada Martin, 20:00, 4 guests, T5
            </>,
            { label: "See it", run: () => setOpen(true) },
          )
        }
      >
        Toast with an action
      </Pill>
        <Dialog open={open} onClose={() => setOpen(false)} title="R-014, Ada Martin">
          <p>Saturday 31 October, 20:00, 4 guests, table T5.</p>
          <div className="dlg-foot">
            <Pill small onClick={() => setOpen(false)}>
              Keep it
            </Pill>
            <Pill small primary onClick={() => setOpen(false)}>
              Cancel the booking
            </Pill>
          </div>
        </Dialog>
    </div>
  );
}

function SaveBarDemo() {
  const draft = useDraft({ max: 4 });
  const [error, setError] = useState<string | null>(null);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!(draft.value.max >= 1)) return setError("The largest group has to be at least 1.");
    setError(null);
    draft.commit();
  };
  return (
    <form onSubmit={submit} className="mg-gallery-grid">
      <Field
        label="Largest group"
        hint="Change it to show the bar; 0 shows the validation message."
        type="number"
        value={draft.value.max}
        onChange={(event) => draft.set({ max: Number(event.target.value) })}
      />
      <SaveBar
        dirty={draft.dirty}
        error={error}
        onDiscard={() => {
          setError(null);
          draft.reset();
        }}
      />
    </form>
  );
}

/**
 * Design-system primitives.
 *
 * Every reusable surface in the command center is composed from these. They
 * exist so that a status pill, a KPI, an evidence drawer or a check row looks
 * and behaves identically everywhere — and so accessibility (semantics, glyph +
 * colour pairing, keyboard behaviour) is solved once rather than per screen.
 */

import type { ReactNode } from "react";

import { OUTCOME_GLYPH, OUTCOME_TONE } from "../lib/format";

/* --------------------------------------------------------------- layout */

/**
 * A scrollable table wrapper that a keyboard user can actually scroll.
 *
 * A container with `overflow: auto` is only scrollable by pointer unless it is
 * focusable, which locks keyboard-only users out of any column that does not
 * fit. axe-core reports this as `scrollable-region-focusable`, and it is the
 * one accessibility rule in this project that no amount of colour or semantic
 * work would have caught — it only exists once the thing is rendered.
 *
 * `role="region"` plus a label turns the scroll window into something a screen
 * reader can announce and navigate to, rather than an anonymous focusable box.
 */
export function TableScroll({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <div className="table__scroll" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

export function Panel({
  eyebrow,
  title,
  subtitle,
  actions,
  children,
  id,
  flush,
  tone,
}: {
  eyebrow?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
  flush?: boolean;
  tone?: "default" | "approval";
}): JSX.Element {
  return (
    <section className={`panel${tone === "approval" ? " panel--flush" : ""}`} id={id}>
      {(title || eyebrow || actions) && (
        <header className="panel__head">
          <div className="panel__titles">
            {eyebrow && <p className="panel__eyebrow">{eyebrow}</p>}
            {title && <h3 className="panel__title">{title}</h3>}
            {subtitle && <p className="panel__subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className={`panel__body${flush ? " panel__body--flush" : ""}`}>{children}</div>
    </section>
  );
}

export function Section({
  number,
  title,
  note,
  children,
  id,
}: {
  number: string;
  title: string;
  note?: string;
  children: ReactNode;
  id?: string;
}): JSX.Element {
  return (
    <section className="section" id={id}>
      <header className="section__head">
        <span className="section__num" aria-hidden="true">
          {number}
        </span>
        <h2 className="section__title">{title}</h2>
        {note && <p className="section__note">{note}</p>}
      </header>
      {children}
    </section>
  );
}

/* ----------------------------------------------------------------- KPI */

export function Kpi({
  label,
  value,
  unit,
  foot,
  accent,
  hero,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  foot?: ReactNode;
  accent?: string;
  hero?: boolean;
}): JSX.Element {
  return (
    <div className={`kpi${hero ? " kpi--hero" : ""}`} style={accent ? { ["--kpi-accent" as string]: accent } : undefined}>
      <div className="kpi__label">{label}</div>
      <div className="kpi__value">
        {value}
        {unit && <span className="kpi__unit">{unit}</span>}
      </div>
      {foot && <div className="kpi__foot">{foot}</div>}
    </div>
  );
}

/* --------------------------------------------------------------- badges */

export function Badge({
  tone = "",
  glyph,
  children,
  title,
}: {
  tone?: string;
  glyph?: string;
  children: ReactNode;
  title?: string;
}): JSX.Element {
  return (
    <span className={`badge ${tone}`} title={title}>
      {glyph && (
        <span className="badge__glyph" aria-hidden="true">
          {glyph}
        </span>
      )}
      {children}
    </span>
  );
}

/** A status badge that always pairs colour with a glyph and a text label. */
export function StatusBadge({ status, label }: { status: string; label?: string }): JSX.Element {
  const tone = OUTCOME_TONE[status] ?? "";
  const glyph = OUTCOME_GLYPH[status] ?? "•";
  return (
    <Badge tone={tone} glyph={glyph}>
      {label ?? status}
    </Badge>
  );
}

/** Fake/mock/simulated/real labels. This is a product feature, not decoration. */
export function Provenance({
  kind,
  text,
}: {
  kind: string;
  text?: string;
}): JSX.Element {
  const labels: Record<string, string> = {
    fixture: "M2 fixture",
    deterministic: "deterministic",
    simulated: "simulated",
    mocked: "mocked",
    real: "real",
  };
  return (
    <span className={`prov prov--${kind}`} title={text}>
      {text ?? labels[kind] ?? kind}
    </span>
  );
}

/* ---------------------------------------------------------------- notes */

export function Note({
  tone = "",
  glyph,
  children,
}: {
  tone?: "" | "note--warn" | "note--danger" | "note--ok" | "note--info";
  glyph?: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <div className={`note ${tone}`} role={tone === "note--danger" ? "alert" : undefined}>
      {glyph && (
        <span className="note__glyph" aria-hidden="true">
          {glyph}
        </span>
      )}
      <div>{children}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- bars */

export function Bar({
  value,
  color,
  tall,
  label,
}: {
  value: number;
  color?: string;
  tall?: boolean;
  label?: string;
}): JSX.Element {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return (
    <div
      className={`bar${tall ? " bar--tall" : ""}`}
      role="meter"
      aria-valuenow={Math.round(clamped * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div
        className="bar__fill"
        style={{
          width: `${clamped * 100}%`,
          ["--bar-color" as string]: color ?? "var(--accent)",
        }}
      />
    </div>
  );
}

export function MeterRow({
  label,
  valueText,
  value,
  color,
}: {
  label: string;
  valueText: string;
  value: number;
  color?: string;
}): JSX.Element {
  return (
    <div>
      <div className="meter">
        <span className="meter__label" title={label}>
          {label}
        </span>
        <span className="meter__value">{valueText}</span>
      </div>
      <Bar value={value} color={color} label={`${label}: ${valueText}`} />
    </div>
  );
}

/* ------------------------------------------------------------- evidence */

export function Evidence({
  title = "Evidence",
  data,
  children,
}: {
  title?: string;
  data?: unknown;
  children?: ReactNode;
}): JSX.Element {
  return (
    <details className="evidence">
      <summary>{title}</summary>
      <div className="evidence__body">
        {data !== undefined && (
          <pre className="json">{JSON.stringify(data, null, 2)}</pre>
        )}
        {children}
      </div>
    </details>
  );
}

export function Disclosure({
  title,
  children,
  open,
}: {
  title: string;
  children: ReactNode;
  open?: boolean;
}): JSX.Element {
  return (
    <details className="disclosure" open={open}>
      <summary>{title}</summary>
      <div className="evidence__body">{children}</div>
    </details>
  );
}

/* ------------------------------------------------------------- def list */

export function DefList({
  rows,
}: {
  rows: Array<[string, ReactNode]>;
}): JSX.Element {
  return (
    <dl className="deflist">
      {rows.map(([term, value], index) => (
        <div key={`${term}-${index}`} style={{ display: "contents" }}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function StatRow({
  label,
  value,
  mono = true,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="statrow">
      <span className="statrow__k">{label}</span>
      <span className="statrow__v" style={mono ? undefined : { fontFamily: "inherit" }}>
        {value}
      </span>
    </div>
  );
}

/* ---------------------------------------------------------------- empty */

export function EmptyState({
  title,
  hint,
  glyph = "○",
}: {
  title: string;
  hint?: string;
  glyph?: string;
}): JSX.Element {
  return (
    <div className="empty">
      <div aria-hidden="true" style={{ fontSize: "1.4rem", marginBottom: 6, opacity: 0.5 }}>
        {glyph}
      </div>
      <div className="empty__title">{title}</div>
      {hint && <div>{hint}</div>}
    </div>
  );
}

export function Spinner({ label }: { label: string }): JSX.Element {
  return (
    <span className="row" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="small secondary">{label}</span>
    </span>
  );
}

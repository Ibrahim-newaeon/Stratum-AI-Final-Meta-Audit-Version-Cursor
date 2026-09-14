/**
 * Marketing homepage — Evidence Room narrative
 */

import { Link } from 'react-router-dom';
import { EvidenceMarketingShell } from '@/components/evidence/EvidenceMarketingShell';
import { DecisionCrossExamination } from '@/components/evidence/DecisionCrossExamination';
import { SEO, pageSEO } from '@/components/common/SEO';
import { SAMPLE_LABEL } from '@/theme/evidence';

const SEQUENCE = [
  { step: '01', title: 'Signal received', detail: 'Source: Meta Ads · 14 Sep 2026, 09:18 UTC' },
  { step: '02', title: 'Signal health checked', detail: 'Status: Degraded · Conversion lag 11h' },
  { step: '03', title: 'Recommendation evaluated', detail: 'Increase daily budget +18%' },
  { step: '04', title: 'Policy & permissions checked', detail: 'Approval required · Scope: ads_management' },
  { step: '05', title: 'Action held', detail: 'Result: Not executed · Reason: evidence window' },
  { step: '06', title: 'Decision recorded', detail: 'ID: DEC-2026-0914-0042 · Audit trail written' },
];

const CONTROLS = [
  { title: 'Approval thresholds', body: 'Require a person when spend impact exceeds configured limits.' },
  { title: 'Role-based permissions', body: 'Separate who can recommend, approve, and reverse.' },
  { title: 'Manual override', body: 'Authorize or refuse a held action with a recorded rationale.' },
  { title: 'Review queues', body: 'Work from holds and pending reviews, not a generic inbox.' },
  { title: 'Automation scopes', body: 'Limit which campaigns and action types Autopilot may touch.' },
  { title: 'Audit history', body: 'Revisit who approved what, and what evidence was available then.' },
];

export default function EvidenceHomePage() {
  return (
    <>
      <SEO {...pageSEO.landing} />
      <EvidenceMarketingShell>
        {/* 01 Challenge */}
        <section className="mx-auto grid max-w-[1200px] grid-cols-1 gap-12 px-6 py-16 lg:grid-cols-12 lg:py-24">
          <div className="lg:col-span-7">
            <p className="er-label">01 / The challenge</p>
            <h1 className="er-serif mt-4 text-4xl leading-[1.1] md:text-6xl" style={{ color: 'var(--er-text)' }}>
              Your budget deserves a cross-examination.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-8" style={{ color: 'var(--er-muted)' }}>
              StratumAI checks the signals behind your ad spend before automation acts. Restraint is
              the product — knowing when a recommendation is supported, uncertain, or should wait
              for a person.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a href="#cross-examination" className="er-btn-primary">
                Inspect a decision
              </a>
              <Link to="/signup" className="er-btn-ghost">
                Start free trial
              </Link>
            </div>
          </div>

          <aside className="er-panel lg:col-span-5" aria-label="Annotated recommendation sample">
            <div className="border-b px-5 py-3" style={{ borderColor: 'var(--er-border)' }}>
              <p className="er-mono text-[11px]" style={{ color: 'var(--er-muted)' }}>
                {SAMPLE_LABEL}
              </p>
            </div>
            <div className="px-5 py-5">
              <p className="er-label">Recommendation</p>
              <p className="mt-2 text-base font-semibold tracking-wide">INCREASE DAILY BUDGET</p>
              <p className="mt-1 text-sm" style={{ color: 'var(--er-muted)' }}>
                Campaign: Spring Acquisition / Search · Proposed: +18%
              </p>
              <p className="er-status er-status-held mt-3">
                <span className="er-status-dot" /> Awaiting evidence review
              </p>
            </div>
            <div className="er-seam-open mx-5" />
            <ul className="er-tray m-5 space-y-2 p-4 text-sm">
              {[
                'Conversion reporting delayed by 11 hours',
                'Tracking anomaly detected in one source',
                'Recent spend pacing is within policy',
                'Recommendation held until signal quality improves',
              ].map((a) => (
                <li key={a} className="flex gap-2">
                  <span style={{ color: 'var(--er-accent)' }}>—</span>
                  <span style={{ color: 'var(--er-muted)' }}>{a}</span>
                </li>
              ))}
            </ul>
          </aside>
        </section>

        <hr className="er-rule mx-auto max-w-[1200px]" />

        {/* 02 Cross-examination */}
        <section id="cross-examination" className="mx-auto max-w-[1200px] px-6 py-16 scroll-mt-20">
          <p className="er-label">02 / The cross-examination</p>
          <h2 className="er-serif mt-3 max-w-2xl text-3xl md:text-4xl">
            Select a condition. Watch the seam open.
          </h2>
          <p className="mt-4 max-w-2xl text-base leading-7" style={{ color: 'var(--er-muted)' }}>
            A recommendation can look plausible while resting on incomplete evidence. Expose the
            weakness yourself — then see what StratumAI refuses to do.
          </p>
          <div className="mt-10">
            <DecisionCrossExamination />
          </div>
        </section>

        {/* 03 Beneath the decision */}
        <section id="beneath" className="border-y py-16" style={{ borderColor: 'var(--er-border)', background: 'var(--er-surface)' }}>
          <div className="mx-auto max-w-[1200px] px-6">
            <p className="er-label">03 / Beneath the decision</p>
            <h2 className="er-serif mt-3 text-3xl md:text-4xl">A document trail, not a process diagram.</h2>
            <ol className="mt-10 space-y-0">
              {SEQUENCE.map((s, i) => (
                <li key={s.step} className="grid grid-cols-[64px_1fr] gap-4 border-t py-5 md:grid-cols-[80px_1fr_1fr]" style={{ borderColor: 'var(--er-border)' }}>
                  <span className="er-mono text-sm" style={{ color: 'var(--er-accent)' }}>
                    {s.step}
                  </span>
                  <span className="font-medium">{s.title}</span>
                  <span className="er-mono text-xs md:text-sm" style={{ color: 'var(--er-muted)' }}>
                    {s.detail}
                  </span>
                  {i === SEQUENCE.length - 1 && null}
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* 04 Accountability */}
        <section className="mx-auto max-w-[1200px] px-6 py-16">
          <p className="er-label">04 / Your name is on the budget</p>
          <h2 className="er-serif mt-3 max-w-2xl text-3xl md:text-4xl">
            Automation can move quickly. Accountability still belongs to a person.
          </h2>
          <div className="mt-10 grid grid-cols-1 gap-px md:grid-cols-2 lg:grid-cols-3" style={{ background: 'var(--er-border)' }}>
            {CONTROLS.map((c) => (
              <div key={c.title} className="p-5" style={{ background: 'var(--er-bg)' }}>
                <h3 className="text-sm font-semibold">{c.title}</h3>
                <p className="mt-2 text-sm leading-6" style={{ color: 'var(--er-muted)' }}>
                  {c.body}
                </p>
              </div>
            ))}
          </div>
          <p className="mt-6 text-sm" style={{ color: 'var(--er-muted)' }}>
            If an external platform action cannot be fully undone, StratumAI records that limitation
            on the decision dossier.
          </p>
        </section>

        {/* 05 Receipts */}
        <section className="mx-auto max-w-[1200px] px-6 py-8">
          <div className="er-panel p-6 md:p-8">
            <p className="er-label">05 / The receipts</p>
            <h2 className="er-serif mt-2 text-2xl md:text-3xl">Worked example. Not customer data.</h2>
            <p className="mt-4 max-w-2xl text-sm leading-7" style={{ color: 'var(--er-muted)' }}>
              Baseline: pacing within policy · Intervention: budget increase proposed · Evidence at
              the time: conversion lag 11 hours · Outcome: held for review · What was not changed:
              live spend · Mode: automatic hold, human authorization required.
            </p>
          </div>
        </section>

        {/* 06 Fit */}
        <section id="fit" className="mx-auto max-w-[1200px] px-6 py-16 scroll-mt-20">
          <p className="er-label">06 / Fit before commitment</p>
          <h2 className="er-serif mt-3 text-3xl">What you need to connect</h2>
          <div className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="er-panel p-5">
              <h3 className="text-sm font-semibold">Connections</h3>
              <ul className="mt-3 space-y-2 text-sm" style={{ color: 'var(--er-muted)' }}>
                <li>Meta Ads OAuth (campaigns & insights)</li>
                <li>Conversions API + Pixel</li>
                <li>Marketing API System User token (Custom Audiences)</li>
                <li>Optional: WhatsApp messaging credentials</li>
              </ul>
            </div>
            <div className="er-panel p-5">
              <h3 className="text-sm font-semibold">Controls & security</h3>
              <ul className="mt-3 space-y-2 text-sm" style={{ color: 'var(--er-muted)' }}>
                <li>Trust gate thresholds and approval scopes</li>
                <li>Encrypted credentials at rest</li>
                <li>Audit log for every executed automation</li>
                <li>Autopilot writes disabled by default</li>
              </ul>
            </div>
          </div>
          <p className="mt-6">
            <Link to="/pricing" className="text-sm font-medium" style={{ color: 'var(--er-accent)' }}>
              View pricing →
            </Link>
          </p>
        </section>

        {/* 07 First decision */}
        <section className="border-t py-20" style={{ borderColor: 'var(--er-border)', background: 'var(--er-surface)' }}>
          <div className="mx-auto max-w-[720px] px-6 text-center">
            <p className="er-label">07 / The first decision</p>
            <h2 className="er-serif mt-3 text-3xl md:text-4xl">Start with a read-only review.</h2>
            <p className="mx-auto mt-4 max-w-lg text-base leading-7" style={{ color: 'var(--er-muted)' }}>
              Connect your data. Inspect the first decision trail. Choose what, if anything,
              StratumAI may automate.
            </p>
            <Link to="/signup" className="er-btn-primary mt-8 inline-flex">
              Start free trial
            </Link>
          </div>
        </section>
      </EvidenceMarketingShell>
    </>
  );
}

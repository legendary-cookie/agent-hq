import type { FleetAgent } from '../api/types';
import { UsageFigure } from '../components/UsageFigure';
import { fmtTokens } from '../lib/format';
import { PLAN_NOTE, fmtPlanPct, unattributedLine, usageLabel, usageView } from '../lib/usage';
import { SortHeader, useSort } from '../lib/useSort';
import { RangePicker, changeText, count, messages, money, percent, perTask, useFleet } from './fleet-shared';
import { GraphsToggle, TrendChart, useGraphs } from './Trends';
import './fleet.css';

const PERIOD = { today: '24 hours', week: '7 days', month: '30 days' };

export function Usage() {
  const { range, setRange, data, error } = useFleet();
  const totals = data?.totals;
  const plan = data?.plan;
  const usage = (agent: FleetAgent) => usageView({ plan, planPct: agent.plan_pct, apiPaid: agent.api_paid, value: agent.value_usd, unpriced: agent.unpriced_tokens });
  const fleetUsage = totals ? usageView({ plan, planPct: plan?.used_pct, apiPaid: plan?.api_cost_week, value: totals.value_usd, unpriced: totals.unpriced_tokens }) : null;
  const ranked = [...(data?.agents ?? [])].filter((agent) => agent.tokens > 0 || (agent.plan_pct ?? 0) > 0 || (agent.api_paid ?? 0) > 0)
    .sort((a, b) => (usage(b).sort ?? -1) - (usage(a).sort ?? -1) || b.value_usd - a.value_usd || b.tokens - a.tokens);
  const byAgent = useSort(ranked, { columns: { agent: (agent) => agent.label, usage: (agent) => usage(agent).sort }, storageKey: 'usage-agents' });
  const byModel = useSort(data?.models ?? [], { columns: { model: (row) => row.model, tokens: (row) => row.tokens, value: (row) => row.value_usd }, storageKey: 'usage-models' });
  const silent = ranked.filter((agent) => agent.value_usd > 0 && !messages(agent));
  const top = Math.max(...ranked.map((agent) => usage(agent).sort ?? 0), 0.01);
  const change = totals ? changeText(totals.value_usd, totals.previous_value_usd) : null;
  const [graphs, setGraphs] = useGraphs('usage');
  const planned = fleetUsage?.mode === 'plan' || fleetUsage?.mode === 'mixed';
  const before = unattributedLine(plan);
  const valueText = totals ? `${money(totals.value_usd, totals.unpriced_tokens)} of usage${planned || fleetUsage?.mode === 'api' ? ' at API prices' : ''} in the last ${PERIOD[range]}${change ? `, ${change} vs the period before` : ''}.` : '';
  return (
    <div className="hq-page">
      <header className="page-head">
        <div>
          <h1>Usage</h1>
          <p className="lede num">
            {!totals ? 'Loading…' : planned ? <><span title={PLAN_NOTE}>{fmtPlanPct(plan?.used_pct)} of this week’s Claude plan limits used{fleetUsage?.mode === 'mixed' ? `, plus ${money(plan?.api_cost_week ?? 0)} paid via API` : ''}.</span> {valueText}</>
              : fleetUsage?.mode === 'api' ? `${money(plan?.api_cost_week ?? 0)} paid via API this week. ${valueText}` : valueText}
          </p>
        </div>
        <div className="head-tools"><GraphsToggle on={graphs} onChange={setGraphs} /><RangePicker value={range} onChange={setRange} /></div>
      </header>
      {error && !data && <p className="muted">Couldn’t load usage. Check that Agent HQ is running, then refresh.</p>}
      {data && totals && (
        <>
          <section className="stats stats-grid" aria-label="Usage measures">
            <div className="stat"><div className="k">Projected 30 days</div><div className="v num">{money(totals.projected_30d_usd)}</div><div className="d">{planned ? 'at API prices, at this period’s rate' : 'at this period’s rate'}</div></div>
            <div className="stat"><div className="k">With no one talking to it</div><div className="v num">{money(totals.silent_usd)}</div><div className="d num">{percent(totals.silent_usd, totals.value_usd)} of usage, {count(totals.silent_agents)} {totals.silent_agents === 1 ? 'agent' : 'agents'}</div></div>
            <div className="stat"><div className="k">Top-tier models</div><div className="v num">{percent(totals.top_tier_usd, totals.value_usd)}</div><div className="d num">{money(totals.top_tier_usd)} on Opus and Fable</div></div>
            <div className="stat"><div className="k">{planned ? 'Per task at API prices' : 'Cost per task'}</div><div className="v num">{perTask(totals.value_usd, totals.tasks)}</div><div className="d num">{count(totals.tasks)} tasks</div></div>
            <div className="stat"><div className="k">Rereading old context</div><div className="v num">{percent(totals.cache_read_tokens, totals.tokens)}</div><div className="d">of tokens are cache reads</div></div>
            <div className="stat"><div className="k">Error rate</div><div className="v num">{percent(totals.errors, totals.actions, 1)}</div><div className="d num">{count(totals.errors)} errors in {count(totals.actions)} actions</div></div>
          </section>
          {graphs && data.series && (
            <div className="trends">
              <TrendChart title="Usage value over time" win={data.window} format={(n) => money(n)} series={[
                { key: 'value_usd', label: 'Value at API prices', values: data.series.value_usd, color: 'var(--text)', kind: 'bar' },
              ]} />
              <TrendChart title="Tokens and errors" win={data.window} format={fmtTokens} formatRight={(n) => n.toLocaleString('en-US')} series={[
                { key: 'tokens', label: 'Tokens', values: data.series.tokens, color: 'var(--muted)' },
                { key: 'errors', label: 'Errors', values: data.series.errors, color: 'var(--error)', right: true },
              ]} />
            </div>
          )}
          <div className="columns">
            <section>
              <div className="section-head"><h2>By agent</h2>{planned && <span className="muted" title={PLAN_NOTE}>plan use this week</span>}</div>
              {ranked.length ? (
                <>
                  <div className="list-head" role="row"><SortHeader sorter={byAgent} col="agent">Agent</SortHeader><SortHeader sorter={byAgent} col="usage">{usageLabel(plan)}</SortHeader></div>
                  {byAgent.rows.map((agent) => {
                    const view = usage(agent);
                    return (
                      <div className={`bar-row${view.short ? ' bar-row-wide' : ''}`} key={agent.host}>
                        <span className="bar-name">{agent.label}</span>
                        <span className="bar"><i style={{ width: `${((view.sort ?? 0) / top) * 100}%` }} /></span>
                        <span className="bar-value"><UsageFigure view={view} compact /></span>
                      </div>
                    );
                  })}
                  {before && <div className="list-row muted" title="Plan use this week that 9router can’t put on any agent: accounts used through the old login pool before they came to 9router."><span>{before.label}</span><span className="num">{before.value}</span></div>}
                </>
              ) : <p className="muted">No usage in this period.</p>}
            </section>
            <section>
              {silent.length > 0 && (
                <div className="side-section">
                  <div className="section-head"><h2>Working with no messages</h2></div>
                  {silent.slice(0, 6).map((agent) => (
                    <div className="list-row" key={agent.host}>
                      <span className="list-name">{agent.label}</span>
                      <span className="num muted"><UsageFigure view={usage(agent)} compact /></span>
                    </div>
                  ))}
                  <p className="note">These agents had no messages with people in this period, so scheduled or background work is driving their usage.</p>
                </div>
              )}
              <div className="section-head"><h2>By model</h2>{planned && <span className="muted">at API prices</span>}</div>
              {data.models.length ? (
                <>
                  <div className="list-head" role="row"><SortHeader sorter={byModel} col="model">Model</SortHeader><span><SortHeader sorter={byModel} col="tokens">Tokens</SortHeader> · <SortHeader sorter={byModel} col="value">Value</SortHeader></span></div>
                  {byModel.rows.map((model) => (
                    <div className="list-row" key={model.model}>
                      <span className="list-name">{model.model}</span>
                      <span className="num muted">{fmtTokens(model.tokens)} tokens · {money(model.value_usd, model.unpriced_tokens)}</span>
                    </div>
                  ))}
                </>
              ) : <p className="muted">No usage in this period.</p>}
              <div className="list-row"><span>Total tokens</span><span className="num muted">{fmtTokens(totals.tokens)}</span></div>
              {totals.unpriced_tokens > 0 && <div className="list-row"><span>Without a price</span><span className="num muted">{count(totals.unpriced_tokens)} tokens</span></div>}
              <p className="note">{planned
                ? <>Plan use is each agent’s share of the Claude plans’ weekly limits this week, whatever period is picked above. It’s estimated from 9router: each agent’s share of each account’s use, weighted by API price; usage before the move to 9router is spread by the same shares. Dollar figures are what the tokens would cost at API prices, not your bill.</>
                : <>Usage value is what these tokens would cost at API prices. It isn’t your bill. <a href="#/settings">Add your plans</a> to compare it with what you pay.</>}</p>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

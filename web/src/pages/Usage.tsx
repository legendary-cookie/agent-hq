import { fmtTokens } from '../lib/format';
import { RangePicker, changeText, count, messages, money, percent, perTask, useFleet } from './fleet-shared';
import { GraphsToggle, TrendChart, useGraphs } from './Trends';
import './fleet.css';

const PERIOD = { today: '24 hours', week: '7 days', month: '30 days' };

export function Usage() {
  const { range, setRange, data, error } = useFleet();
  const totals = data?.totals;
  const ranked = [...(data?.agents ?? [])].filter((agent) => agent.tokens > 0).sort((a, b) => b.value_usd - a.value_usd || b.tokens - a.tokens);
  const silent = ranked.filter((agent) => agent.value_usd > 0 && !messages(agent));
  const top = Math.max(...ranked.map((agent) => agent.value_usd), 0.01);
  const change = totals ? changeText(totals.value_usd, totals.previous_value_usd) : null;
  const [graphs, setGraphs] = useGraphs('usage');
  return (
    <div className="hq-page">
      <header className="page-head">
        <div>
          <h1>Usage</h1>
          <p className="lede num">{totals ? `${money(totals.value_usd, totals.unpriced_tokens)} of usage in the last ${PERIOD[range]}${change ? `, ${change} vs the period before` : ''}.` : 'Loading…'}</p>
        </div>
        <div className="head-tools"><GraphsToggle on={graphs} onChange={setGraphs} /><RangePicker value={range} onChange={setRange} /></div>
      </header>
      {error && !data && <p className="muted">Couldn’t load usage. Check that Agent HQ is running, then refresh.</p>}
      {data && totals && (
        <>
          <section className="stats stats-grid" aria-label="Usage measures">
            <div className="stat"><div className="k">Projected 30 days</div><div className="v num">{money(totals.projected_30d_usd)}</div><div className="d">at this period’s rate</div></div>
            <div className="stat"><div className="k">With no one talking to it</div><div className="v num">{money(totals.silent_usd)}</div><div className="d num">{percent(totals.silent_usd, totals.value_usd)} of usage, {count(totals.silent_agents)} {totals.silent_agents === 1 ? 'agent' : 'agents'}</div></div>
            <div className="stat"><div className="k">Top-tier models</div><div className="v num">{percent(totals.top_tier_usd, totals.value_usd)}</div><div className="d num">{money(totals.top_tier_usd)} on Opus and Fable</div></div>
            <div className="stat"><div className="k">Cost per task</div><div className="v num">{perTask(totals.value_usd, totals.tasks)}</div><div className="d num">{count(totals.tasks)} tasks</div></div>
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
              <div className="section-head"><h2>By agent</h2></div>
              {ranked.length ? ranked.map((agent) => (
                <div className="bar-row" key={agent.host}>
                  <span className="bar-name">{agent.label}</span>
                  <span className="bar"><i style={{ width: `${(agent.value_usd / top) * 100}%` }} /></span>
                  <span className="num bar-value">{money(agent.value_usd, agent.unpriced_tokens)}</span>
                </div>
              )) : <p className="muted">No usage in this period.</p>}
            </section>
            <section>
              {silent.length > 0 && (
                <div className="side-section">
                  <div className="section-head"><h2>Working with no messages</h2></div>
                  {silent.slice(0, 6).map((agent) => (
                    <div className="list-row" key={agent.host}>
                      <span className="list-name">{agent.label}</span>
                      <span className="num muted">{money(agent.value_usd)}</span>
                    </div>
                  ))}
                  <p className="note">These agents had no messages with people in this period, so scheduled or background work is driving their usage.</p>
                </div>
              )}
              <div className="section-head"><h2>By model</h2></div>
              {data.models.length ? data.models.map((model) => (
                <div className="list-row" key={model.model}>
                  <span className="list-name">{model.model}</span>
                  <span className="num muted">{money(model.value_usd, model.unpriced_tokens)}</span>
                </div>
              )) : <p className="muted">No usage in this period.</p>}
              <div className="list-row"><span>Total tokens</span><span className="num muted">{fmtTokens(totals.tokens)}</span></div>
              {totals.unpriced_tokens > 0 && <div className="list-row"><span>Without a price</span><span className="num muted">{count(totals.unpriced_tokens)} tokens</span></div>}
              <p className="note">Usage value is what these tokens would cost at API prices. It isn’t your bill. <a href="#/settings">Add your plans</a> to compare it with what you pay.</p>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

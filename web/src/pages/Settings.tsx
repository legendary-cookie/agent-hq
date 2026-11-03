import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, useApi } from '../api/client';
import type { Health, Kpis, RunOptions, Settings as SettingsData } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import './settings.css';

type Price = SettingsData['prices'][string];
const priceFields: { key: keyof Price; label: string }[] = [
  { key: 'input', label: 'Input' },
  { key: 'output', label: 'Output' },
  { key: 'cache_read', label: 'Cache read' },
  { key: 'cache_write', label: 'Cache write' },
];

export function Settings() {
  const {
    data,
    error: settingsError,
    reload: reloadSettings,
  } = useApi<SettingsData>('/api/settings');
  const { data: kpis, error: kpisError } = useApi<Kpis>('/api/kpis?range=month');
  const { data: health, error: healthError, reload: reloadHealth } = useApi<Health>('/api/health');
  const { data: runOptions } = useApi<RunOptions>('/api/runs/options', { live: false });
  const [draft, setDraft] = useState<SettingsData | null>(null);
  const [pendingPrices, setPendingPrices] = useState<
    Record<string, Partial<Record<keyof Price, string>>>
  >({});
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState('');
  useEffect(() => {
    if (!installing) return;
    const timer = setInterval(reloadHealth, 2000);
    return () => clearInterval(timer);
  }, [installing, reloadHealth]);

  useEffect(() => {
    if (installing && health?.importer?.state === 'ok') {
      setInstalling(false);
      reloadSettings();
    }
  }, [installing, health, reloadSettings]);

  async function installImporter() {
    setInstallError('');
    try {
      await api('/api/importer/install', { method: 'POST', body: '{}' });
      setInstalling(true);
      reloadHealth();
    } catch (err) {
      setInstallError(err instanceof Error ? err.message : 'Could not start installation.');
    }
  }
  useEffect(() => {
    if (data) setDraft((current) => (current ? { ...current, sources: data.sources } : data));
  }, [data]);
  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 3000);
    return () => clearTimeout(timer);
  }, [saved]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    if (
      Object.values(pendingPrices).some((price) =>
        priceFields.some(({ key }) => price[key] === undefined || price[key] === ''),
      )
    ) {
      setError('Enter all four prices for each new model.');
      return;
    }
    const prices = { ...draft.prices };
    for (const [model, price] of Object.entries(pendingPrices)) {
      prices[model] = {
        input: Number(price.input),
        output: Number(price.output),
        cache_read: Number(price.cache_read),
        cache_write: Number(price.cache_write),
      };
    }
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify({ ...draft, prices }) });
      setDraft({ ...draft, prices });
      setPendingPrices({});
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save settings.');
    } finally {
      setSaving(false);
    }
  }

  const unpriced = (kpis?.top_models ?? []).filter(
    (item) => draft && !(item.model in draft.prices),
  );
  const models = [
    ...new Set([
      ...unpriced.map((item) => item.model),
      ...Object.keys(draft?.price_sources ?? {}),
      ...Object.keys(draft?.prices ?? {}),
    ]),
  ];
  return (
    <div className="settings-page">
      <h1>Settings</h1>
      {runOptions?.projects && runOptions?.profiles && <section className="settings-card"><h2>Run mode</h2><p>{runOptions.run_mode ? 'On' : 'Off'}</p><p>Projects: {runOptions.projects.join(', ') || 'None'}</p><p>Profiles: {runOptions.profiles.map((p) => `${p.id} (${p.tool})`).join(', ') || 'None'}</p><p>Turn run mode on and list projects in config/run.json and config/projects.json; profiles live in config/accounts.json.</p></section>}
      {(settingsError || kpisError || healthError) && (
        <p role="alert">{(settingsError || kpisError || healthError)?.message}</p>
      )}
      {!draft && !settingsError && <p>Loading settings…</p>}
      {draft && (
        <form onSubmit={save}>
          <section className="settings-card">
            <h2>Plans</h2>
            {draft.accounts.length === 0 ? (
              <EmptyState title="No accounts" />
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Plan</th>
                    <th className="settings-number">Monthly price ($)</th>
                    <th className="settings-number">Billing day</th>
                  </tr>
                </thead>
                <tbody>
                  {draft.accounts.map((account, index) => (
                    <tr key={account.id}>
                      {(['label', 'plan', 'monthly_price_usd', 'billing_day'] as const).map(
                        (field) => (
                          <td key={field}>
                            <input
                              aria-label={`${account.id} ${field}`}
                              type={
                                field === 'monthly_price_usd' || field === 'billing_day'
                                  ? 'number'
                                  : 'text'
                              }
                              min={
                                field === 'billing_day'
                                  ? 1
                                  : field === 'monthly_price_usd'
                                    ? 0
                                    : undefined
                              }
                              max={field === 'billing_day' ? 31 : undefined}
                              step={field === 'monthly_price_usd' ? 'any' : undefined}
                              value={account[field] ?? ''}
                              onChange={(event) => {
                                const value = event.target.value;
                                setDraft({
                                  ...draft,
                                  accounts: draft.accounts.map((item, i) =>
                                    i !== index
                                      ? item
                                      : {
                                          ...item,
                                          [field]:
                                            field === 'monthly_price_usd'
                                              ? value === ''
                                                ? null
                                                : Number(value)
                                              : field === 'billing_day'
                                                ? Number(value)
                                                : field === 'plan'
                                                  ? value || null
                                                  : value,
                                        },
                                  ),
                                });
                              }}
                            />
                          </td>
                        ),
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section className="settings-card">
            <h2>Model prices</h2>
            <p className="settings-hint">Prices per million tokens.</p>
            {models.length === 0 ? (
              <EmptyState title="No models" />
            ) : (
              <div className="settings-models">
                {models.map((model) => (
                  <div className="settings-model" key={model}>
                    <div>
                      <strong>{model}</strong>
                      <span className="settings-hint">
                        {' · '}
                        {draft.prices[model]
                          ? 'yours'
                          : draft.price_sources?.[model] === 'litellm'
                            ? 'LiteLLM'
                            : 'unpriced'}
                      </span>
                    </div>
                    <div className="settings-price-fields">
                      {priceFields.map(({ key, label }) => (
                        <label key={key}>
                          {label}
                          <input
                            aria-label={`${model} ${label}`}
                            type="number"
                            min="0"
                            step="any"
                            value={draft.prices[model]?.[key] ?? pendingPrices[model]?.[key] ?? ''}
                            onChange={(event) => {
                              const value = event.target.value;
                              if (!draft.prices[model]) {
                                setPendingPrices({
                                  ...pendingPrices,
                                  [model]: { ...pendingPrices[model], [key]: value },
                                });
                                return;
                              }
                              setDraft({
                                ...draft,
                                prices: {
                                  ...draft.prices,
                                  [model]: {
                                    ...(draft.prices[model] ?? {
                                      input: 0,
                                      output: 0,
                                      cache_read: 0,
                                      cache_write: 0,
                                    }),
                                    [key]: Number(value),
                                  },
                                },
                              });
                            }}
                          />
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
          <section className="settings-card">
            <h2>Alerts</h2>
            <div className="settings-alerts">
              {draft.alerts.thresholds.map((threshold, index) => (
                <label key={index}>
                  Threshold {index + 1} (%)
                  <input
                    type="number"
                    min="0"
                    max="100"
                    step="any"
                    value={threshold}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        alerts: {
                          ...draft.alerts,
                          thresholds: draft.alerts.thresholds.map((value, i) =>
                            i === index ? Number(event.target.value) : value,
                          ),
                        },
                      })
                    }
                  />
                </label>
              ))}
              <label>
                DeepSeek low-balance alert
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={draft.deepseek_alert ?? 5}
                  onChange={(event) =>
                    setDraft({ ...draft, deepseek_alert: Number(event.target.value) })
                  }
                />
              </label>
              <label className="settings-toggle">
                <input
                  type="checkbox"
                  checked={draft.alerts.toast}
                  onChange={(event) =>
                    setDraft({ ...draft, alerts: { ...draft.alerts, toast: event.target.checked } })
                  }
                />{' '}
                Toast notifications
              </label>
            </div>
          </section>
          <section className="settings-card">
            <h2>Default office skin</h2>
            <label>
              Skin
              <select
                value={draft.default_skin}
                onChange={(event) => setDraft({ ...draft, default_skin: event.target.value })}
              >
                <option value="isometric">Isometric</option>
                <option value="topdown">Top-down</option>
                <option value="tower">Tower</option>
              </select>
            </label>
          </section>
          <section className="settings-card">
            <h2>Sources</h2>
            <p className="settings-hint">Edit config/sources.json and restart to change sources.</p>
            <label className="settings-toggle">
              <input
                type="checkbox"
                checked={draft.zylos_previews ?? false}
                onChange={(event) => setDraft({ ...draft, zylos_previews: event.target.checked })}
              />
              Show Zylos message previews
            </label>
            <button
              type="button"
              onClick={installImporter}
              disabled={installing && health?.importer?.state !== 'partial'}
            >
              Install long-tail importer
            </button>
            {installing && (
              <p role="status">
                Importer: {health?.importer?.state ?? 'starting'}
                {health?.importer?.error ? ` · ${health.importer.error}` : ''}
              </p>
            )}
            {installError && <p role="alert">{installError}</p>}
            {Object.entries(draft.sources).length === 0 ? (
              <EmptyState title="No sources" />
            ) : (
              Object.entries(draft.sources).map(([name, source]) => {
                const entry = health?.[name];
                const state =
                  entry &&
                  typeof entry === 'object' &&
                  'state' in entry &&
                  typeof entry.state === 'string'
                    ? entry.state
                    : 'unknown';
                return (
                  <div className="settings-source" key={name}>
                    <div>
                      <strong>{name}</strong>{' '}
                      <span className="settings-hint">
                        · {state}
                        {entry && typeof entry === 'object' && entry.tested === 'upstream'
                          ? ' · untested on this PC'
                          : ''}
                      </span>
                    </div>
                    {source.paths.length ? (
                      source.paths.map((path) => (
                        <div className="settings-path" key={path}>
                          {path}
                        </div>
                      ))
                    ) : (
                      <div className="settings-hint">No paths configured</div>
                    )}
                  </div>
                );
              })
            )}
          </section>
          {error && <p role="alert">{error}</p>}
          <div className="settings-actions">
            <button type="submit" disabled={saving}>
              Save settings
            </button>
            {saved && <span role="status">Saved</span>}
          </div>
        </form>
      )}
    </div>
  );
}

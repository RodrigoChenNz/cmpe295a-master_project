import { useEffect } from 'react';
import { api } from '../lib/api';
import { useResource } from '../lib/useStream';
import { usePageChrome, useStreamEvents } from '../components/AppShell';
import { Button, ErrorState, SkeletonRows } from '../components/ui';
import s from './System.module.css';

export function System() {
  const res = useResource(() => api.systemStatus(), []);
  usePageChrome({ title: 'Software status', actions: <Button onClick={res.reload}>Refresh</Button> });
  useStreamEvents(['price', 'proposal', 'labels', 'inventory', 'broker'], res.reload);
  useEffect(() => { const timer = window.setInterval(res.reload, 10000); return () => window.clearInterval(timer); }, [res.reload]);
  if (res.error) return <ErrorState title="Cannot load software status" body={res.error} actions={<Button onClick={res.reload}>Retry</Button>} />;
  if (!res.data) return <SkeletonRows rows={4} columns={3} />;
  const { counts, broker, pricing_models, forecast_models } = res.data;
  return <div className={s.layout}>
    <section className={`card ${s.section}`}>
      <h2>Integration status</h2>
      <p>Follow observations through recommendations, approval, publication, and display acknowledgement.</p>
      <dl className={s.metrics}>
        <div><dt>Message broker</dt><dd>{broker.connected ? 'Connected' : 'Disconnected'}</dd></div>
        <div><dt>Queued deliveries</dt><dd>{counts.queued}</dd></div>
        <div><dt>Published messages</dt><dd>{counts.delivered}</dd></div>
        <div><dt>Store observations</dt><dd>{counts.observations}</dd></div>
        <div><dt>Labels with render reports</dt><dd>{counts.confirmed_labels}</dd></div>
        <div><dt>Emulated labels</dt><dd>{counts.emulated_labels}</dd></div>
      </dl>
      <p>Last observation: {counts.last_observation ? new Date(counts.last_observation).toLocaleString() : 'Waiting for store data or the simulator'}</p>
      {counts.emulated_labels > 0 && <p>Emulator acknowledgements verify the software path. Physical hardware has not been verified by those reports.</p>}
    </section>
    <section className={`card ${s.section}`}>
      <h2>Observed model versions</h2>
      <div className={s.models}>
        <div><h3>Pricing</h3>{pricing_models.length ? <ul>{pricing_models.map(m => <li key={m.agent_version}>{m.agent_version} — {m.proposals} recommendations</li>)}</ul> : <p>Waiting for a pricing service.</p>}</div>
        <div><h3>Forecasting</h3>{forecast_models.length ? <ul>{forecast_models.map(m => <li key={m.model_version}>{m.model_version} — {m.observations} forecasts</li>)}</ul> : <p>Waiting for a forecasting service.</p>}</div>
      </div>
      <p>Version counts show received messages. Model accuracy is measured separately on held-out data.</p>
    </section>
  </div>;
}

-- Real sensor/AI-reading history, for charts. One row per numeric field
-- in a telemetry payload (temp_c, humidity_pct, health_score, ...) --
-- flattened rather than kept as JSON, because a chart needs
-- "device X, metric Y, over time," and a hypertable indexed exactly that
-- way is a straight query, not a JSON-unpacking one, on every page load.
--
-- Non-numeric fields (an AI result's label/note strings) are NOT stored
-- here -- they're not chartable, and the gateway's devices.last_reading
-- column already keeps the full latest payload, strings included, for
-- the "Plant health" card. This table exists for the one thing it's
-- good at: numbers over time.
create table readings (
    device_id text not null,
    ts timestamptz not null default now(),
    metric text not null,
    value double precision not null
);

select create_hypertable('readings', 'ts');

create index readings_device_metric_ts_idx on readings (device_id, metric, ts desc);

-- A local greenhouse box has finite disk. Keeping 30 days of
-- minute-by-minute readings is plenty for "what happened today/this
-- week" charts without needing a human to remember to prune anything.
select add_retention_policy('readings', interval '30 days');

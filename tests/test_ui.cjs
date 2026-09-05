const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const state = vm.createContext({});
vm.runInContext(fs.readFileSync('plasmoid/contents/ui/UsageState.js', 'utf8'), state);

function provider(windows = [{label: 'Session', usedPercent: 12}]) {
    return {id: 'codex', windows, ok: true, primaryLabel: 'Session', primaryUsedPercent: 12};
}
function parse(providers) {
    return state.parsePayload(JSON.stringify({version: 1, providers}));
}

test('polling and repeated manual refreshes share one active request', () => {
    const requests = state.createRequests();
    assert.equal(requests.request('poll', 1).command, 'poll');
    assert.equal(requests.request('refresh', 1), null);
    assert.equal(requests.request('poll', 1), null);
    assert.equal(requests.active.command, 'poll');
    assert.equal(requests.complete(), null);
    assert.equal(requests.request('refresh', 1).command, 'refresh');
});

test('configuration changes coalesce to the latest generation', () => {
    const requests = state.createRequests();
    requests.request('all providers', 1);
    requests.request('codex', 2);
    requests.request('claude', 3);
    assert.equal(requests.active.generation, 1);
    assert.equal(requests.complete().command, 'claude');
    assert.equal(requests.active.generation, 3);
    assert.equal(requests.complete(), null);
});

test('disabling all providers cancels pending work without accepting the old generation', () => {
    const requests = state.createRequests();
    requests.request('all providers', 1);
    requests.request('codex', 2);
    requests.request(null, 3);
    assert.notEqual(requests.active.generation, 3);
    assert.equal(requests.complete(), null);
    assert.equal(requests.request('all providers', 4).generation, 4);
});

test('serialized failed refresh keeps the last reading usable and stale', () => {
    const reading = parse([{...provider(), ok: false, error: 'offline', stale: true}])[0];
    assert.equal(reading.hasReading, true);
    assert.equal(reading.stale, true);
    assert.equal(reading.primaryUsedPercent, 12);
});

test('an unavailable provider has no fabricated percentage', () => {
    const reading = parse([{id: 'grok', windows: [], ok: false, error: 'No usage yet'}])[0];
    assert.equal(reading.hasReading, false);
    assert.equal(reading.maxUsedPercent, null);
});

test('secondary limit warning data does not change the session reading', () => {
    const reading = parse([provider([
        {label: 'Session', usedPercent: 12},
        {label: 'Weekly', usedPercent: 98},
        {label: 'Spark', usedPercent: 90}
    ])])[0];
    assert.equal(reading.primaryUsedPercent, 12);
    assert.equal(reading.maxUsedPercent, 98);
});

test('malformed helper output cannot replace a last-good reading', () => {
    for (const raw of ['null', '{}', '{"version":2,"providers":[]}', '{"version":1,"providers":{}}'])
        assert.throws(() => state.parsePayload(raw));
    assert.throws(() => parse([provider(), provider()]));
    for (const value of [null, '12', -1, 101])
        assert.throws(() => parse([provider([{label: 'Session', usedPercent: value}])]));
    assert.throws(() => parse([{...provider(), primaryUsedPercent: 42}]));
});

test('relative timestamps handle invalid dates, future checks, and passed resets', () => {
    const now = Date.parse('2026-12-31T23:59:00Z');
    assert.equal(state.elapsedMinutes('2026-12-31T23:56:00Z', now), 3);
    assert.equal(state.elapsedMinutes('2027-01-01T00:01:00Z', now), 0);
    assert.equal(state.resetMinutes('2027-01-01T00:01:00Z', now), 2);
    assert.equal(state.resetMinutes('2026-12-31T23:59:00Z', now), 0);
    assert.equal(state.resetMinutes('2026-12-31T23:58:00Z', now), -1);
    for (const invalid of [null, '', 'not a date']) {
        assert.equal(state.resetMinutes(invalid, now), null);
        assert.equal(state.elapsedMinutes(invalid, now), null);
    }
});

test('relative timestamps use instants across daylight saving changes', () => {
    const now = Date.parse('2026-11-01T01:30:00-05:00');
    assert.equal(state.resetMinutes('2026-11-01T01:30:00-06:00', now), 60);
});

// Shared, deterministic state logic, exercised with Node's built-in test runner.
function createRequests() {
    return {
        active: null,
        pending: null,
        request: function(command, generation) {
            if (!command) {
                this.pending = null;
                return null;
            }
            const next = {command: command, generation: generation};
            if (this.active) {
                // Repeated clicks and timer ticks share the current refresh. Only a
                // configuration change needs a follow-up with different inputs.
                if (generation !== this.active.generation)
                    this.pending = next;
                return null;
            }
            this.active = next;
            return next;
        },
        complete: function() {
            this.active = this.pending;
            this.pending = null;
            return this.active;
        }
    };
}

function percent(value) {
    return typeof value === "number" && isFinite(value) && value >= 0 && value <= 100;
}

function parsePayload(stdout) {
    const payload = JSON.parse(stdout);
    if (!payload || payload.version !== 1 || !Array.isArray(payload.providers))
        throw new Error("Unsupported helper response");
    const ids = {};
    return payload.providers.map(function(provider) {
        if (!provider || typeof provider.id !== "string" || ids[provider.id]
                || !Array.isArray(provider.windows))
            throw new Error("Invalid provider response");
        ids[provider.id] = true;
        provider.windows.forEach(function(window) {
            if (!window || typeof window.label !== "string" || !percent(window.usedPercent))
                throw new Error("Invalid usage window");
        });
        const hasReading = provider.windows.length > 0;
        if (hasReading && (!percent(provider.primaryUsedPercent)
                || !provider.windows.some(function(window) {
                    return window.label === provider.primaryLabel
                        && window.usedPercent === provider.primaryUsedPercent;
                })))
            throw new Error("Invalid primary reading");
        return Object.assign({}, provider, {
            hasReading: hasReading,
            stale: hasReading && (!!provider.stale || !!provider.error),
            maxUsedPercent: hasReading
                ? Math.max.apply(null, provider.windows.map(window => window.usedPercent)) : null
        });
    });
}

function elapsedMinutes(iso, now) {
    if (!iso)
        return null;
    const timestamp = new Date(iso).getTime();
    return isNaN(timestamp) ? null : Math.max(0, Math.floor((now - timestamp) / 60000));
}

function resetMinutes(iso, now) {
    if (!iso)
        return null;
    const timestamp = new Date(iso).getTime();
    return isNaN(timestamp) ? null : Math.ceil((timestamp - now) / 60000);
}

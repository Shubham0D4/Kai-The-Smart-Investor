import { useEffect, useState } from 'react';

/**
 * Call `fetcher` now and then every `intervalMs` (0 = once). Returns { data, error, loading }.
 * `fetcher` must be stable (a module function or a useCallback): a new fetcher starts a fresh
 * load, and the previous fetcher's data is never shown for the new one. Changing `refreshKey`
 * re-fetches but keeps showing the current data meanwhile. On a failed refresh the last good
 * data is kept alongside the error; nothing is substituted.
 */
export const usePolled = (fetcher, intervalMs = 0, refreshKey = null) => {
    const [state, setState] = useState({ source: null, data: null, error: null });

    useEffect(() => {
        let cancelled = false;
        const run = async () => {
            try {
                const data = await fetcher();
                if (!cancelled) setState({ source: fetcher, data, error: null });
            } catch (error) {
                if (!cancelled) {
                    setState((prev) => ({
                        source: fetcher,
                        data: prev.source === fetcher ? prev.data : null,
                        error: error.message,
                    }));
                }
            }
        };
        run();
        const timer = intervalMs > 0 ? setInterval(run, intervalMs) : null;
        return () => {
            cancelled = true;
            if (timer) clearInterval(timer);
        };
    }, [fetcher, intervalMs, refreshKey]);

    const current = state.source === fetcher ? state : { data: null, error: null };
    return { data: current.data, error: current.error, loading: state.source !== fetcher };
};

export default usePolled;

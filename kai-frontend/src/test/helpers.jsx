import React from 'react';
import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { CommitteeContext } from '../context/committee';

import nvdaBalanced from './fixtures/nvda_balanced.json';
import aaplZen from './fixtures/aapl_zen.json';
import tslaAlpha from './fixtures/tsla_alpha.json';

// Real /status bodies captured from live debates (real models, real data) by the backend's live
// test suite (tests/conftest.py, tests/artifacts/runs/). Nothing here is invented.
export const fixtures = { nvdaBalanced, aaplZen, tslaAlpha };

/** A committee context value for a given /status body, with every action a spy. */
export const committeeValue = (session, overrides = {}) => ({
    sessionId: session?.session_id ?? null,
    session: session ?? null,
    debate: session?.debate ?? null,
    sigma: session?.sigma ?? null,
    currentTicker: session?.ticker ?? null,
    starting: false,
    startError: null,
    pollError: null,
    approvalError: null,
    decidingApproval: false,
    dismissedChallenge: null,
    startAnalysis: vi.fn(),
    loadSession: vi.fn(),
    clearSession: vi.fn(),
    retryPolling: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    dismissApproval: vi.fn(),
    reopenApproval: vi.fn(),
    ...overrides,
});

export const renderWithSession = (ui, session, overrides = {}) => {
    const value = committeeValue(session, overrides);
    const result = render(
        <MemoryRouter>
            <CommitteeContext.Provider value={value}>{ui}</CommitteeContext.Provider>
        </MemoryRouter>,
    );
    return { ...result, value };
};

/** Text that must never reach the screen: it means a value was not handled. */
export const BROKEN_TEXT = /\bundefined\b|\bNaN\b|\[object Object\]/;

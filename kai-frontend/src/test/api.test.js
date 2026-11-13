import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import api, * as client from '../services/api';

let seen;
const respond = (status, data) => async (config) => {
    seen.push(config);
    const response = { data, status, statusText: String(status), headers: {}, config };
    if (status >= 400) {
        const error = new Error(`HTTP ${status}`);
        error.response = response;
        error.config = config;
        throw error;
    }
    return response;
};

beforeEach(() => { seen = []; });
afterEach(() => { api.defaults.adapter = undefined; });

describe('API client: single-user local app', () => {
    it('sends no credentials: no Authorization header and no user id anywhere', async () => {
        api.defaults.adapter = respond(200, {});
        await client.initiateAnalysis(' aapl ', 'Zen');
        await client.getAnalysisStatus('s1');
        await client.getAnalysisHistory();
        await client.getChatHistory();
        await client.chatWithAI('hi');
        await client.approveMandate('s1', 'c1');
        await client.rejectMandate('s1');
        for (const config of seen) {
            const headers = JSON.stringify(config.headers);
            expect(headers.toLowerCase()).not.toContain('authorization');
            expect(`${config.url}${config.data || ''}${JSON.stringify(config.params || {})}`).not.toMatch(/user_id|username/);
        }
    });

    it('starts an analysis with only the ticker and the persona (no price, EPS or growth inputs)', async () => {
        api.defaults.adapter = respond(200, { session_id: 's1' });
        await client.initiateAnalysis(' aapl ', 'Balanced');
        expect(seen[0].method).toBe('post');
        expect(seen[0].url).toBe('/api/v1/analyze');
        expect(JSON.parse(seen[0].data)).toEqual({ ticker: 'aapl', persona: 'balanced' });
    });

    it('defaults to the local backend and sends approvals as the backend expects', async () => {
        expect(api.defaults.baseURL).toMatch(/^http:\/\/127\.0\.0\.1:8080$|^http:\/\/localhost:8080$/);
        api.defaults.adapter = respond(200, {});
        await client.approveMandate('s1', 'chal_1');
        expect(seen[0].url).toBe('/api/v1/approve/s1');
        expect(JSON.parse(seen[0].data)).toEqual({ challenge_id: 'chal_1', user_confirmation: 'approved' });
        await client.rejectMandate('s1');
        expect(seen[1].url).toBe('/api/v1/reject/s1');
    });

    it('chat sends just the message', async () => {
        api.defaults.adapter = respond(200, { response: 'x' });
        await client.chatWithAI('hello');
        expect(JSON.parse(seen[0].data)).toEqual({ message: 'hello' });
    });
});

describe('errors carry the server\'s own message', () => {
    it('uses FastAPI detail strings and keeps the HTTP status', async () => {
        api.defaults.adapter = respond(502, { detail: 'Market data provider (yfinance) failed for quote AAPL: boom' });
        await expect(client.getQuote('AAPL')).rejects.toMatchObject({
            name: 'ApiError', status: 502, message: 'Market data provider (yfinance) failed for quote AAPL: boom',
        });
    });

    it('joins validation errors', async () => {
        api.defaults.adapter = respond(422, { detail: [{ msg: 'String should match pattern' }, { msg: 'too long' }] });
        await expect(client.initiateAnalysis('???', 'Zen')).rejects.toThrow('String should match pattern; too long');
    });

    it('says the backend is unreachable when there is no response', async () => {
        api.defaults.adapter = async () => { throw new Error('Network Error'); };
        await expect(client.getIndices()).rejects.toThrow(/Cannot reach the Kai backend/);
    });

    it('never substitutes data for a failed market call', async () => {
        api.defaults.adapter = respond(502, { detail: 'down' });
        await expect(client.getIndices()).rejects.toThrow('down');           // no [] and no hardcoded index values
        await expect(client.getPriceHistory('AAPL', '1mo')).rejects.toThrow('down');
        await expect(client.getMarketNews()).rejects.toThrow('down');
    });
});

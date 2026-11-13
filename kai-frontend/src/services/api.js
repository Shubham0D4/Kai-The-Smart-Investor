import axios from 'axios';

// Kai is a single-user local app: no login, no tokens, no user id.
const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8080';

const api = axios.create({
    baseURL: API_URL,
    headers: { 'Content-Type': 'application/json' },
    timeout: 30000,
});

/** Error with the server's own message (FastAPI puts it in `detail`) and the HTTP status. */
export class ApiError extends Error {
    constructor(message, status) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
    }
}

const detailText = (detail) => {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
    return null;
};

api.interceptors.response.use(
    (response) => response,
    (error) => {
        const status = error.response?.status;
        const message = detailText(error.response?.data?.detail)
            || (error.response ? `Request failed (HTTP ${status})` : 'Cannot reach the Kai backend. Is it running?');
        return Promise.reject(new ApiError(message, status));
    }
);

// Analysis sessions
export const initiateAnalysis = async (ticker, persona) => {
    const { data } = await api.post('/api/v1/analyze', {
        ticker: ticker.trim(),
        persona: persona.toLowerCase(),
    });
    return data;
};

export const getAnalysisStatus = async (sessionId) => {
    const { data } = await api.get(`/api/v1/status/${sessionId}`);
    return data;
};

export const getSessionLogs = async (sessionId) => {
    const { data } = await api.get(`/api/v1/sessions/${sessionId}/logs`);
    return data;
};

export const getAnalysisHistory = async () => {
    const { data } = await api.get('/api/v1/history');
    return data;
};

export const approveMandate = async (sessionId, challengeId) => {
    const { data } = await api.post(`/api/v1/approve/${sessionId}`, {
        challenge_id: challengeId,
        user_confirmation: 'approved',
    });
    return data;
};

export const rejectMandate = async (sessionId) => {
    const { data } = await api.post(`/api/v1/reject/${sessionId}`);
    return data;
};

// Market data. Failures reject with the backend's message; nothing is substituted.
export const getIndices = async () => {
    const { data } = await api.get('/api/v1/market/indices');
    return data;
};

export const getQuote = async (symbol) => {
    const { data } = await api.get(`/api/v1/ticker/${encodeURIComponent(symbol)}/quote`);
    return data;
};

export const getPriceHistory = async (symbol, period = '1d') => {
    const { data } = await api.get(`/api/v1/ticker/${encodeURIComponent(symbol)}/history`, { params: { period } });
    return data;
};

export const getTickerNews = async (symbol) => {
    const { data } = await api.get(`/api/v1/ticker/${encodeURIComponent(symbol)}/news`);
    return data;
};

export const getMarketNews = async () => {
    const { data } = await api.get('/api/v1/market/news');
    return data;
};

// Chat
export const getChatHistory = async () => {
    const { data } = await api.get('/api/v1/chat/history');
    return data;
};

export const chatWithAI = async (message) => {
    const { data } = await api.post('/api/v1/chat', { message });
    return data;
};

export default api;

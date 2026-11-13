import React, { useEffect, useRef, useState } from 'react';
import { Bot, MessageCircle, Send, User, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { chatWithAI, getChatHistory } from '../services/api';

const WELCOME = 'Hello! I am Kai, your investment assistant. I do not have live data in this chat; for a ticker, run an analysis.';

const markdownComponents = {
    p: ({ node, ...props }) => <p className="mb-1" {...props} />,
    ul: ({ node, ...props }) => <ul className="mb-1 ps-3" {...props} />,
    li: ({ node, ...props }) => <li className="mb-0" {...props} />,
    strong: ({ node, ...props }) => <strong className="fw-bold" {...props} />,
};

/** Chat with the assistant. History is stored by the backend (SQLite). */
const ChatWidget = () => {
    const [isOpen, setIsOpen] = useState(false);
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [historyError, setHistoryError] = useState(null);
    const bottomRef = useRef(null);

    useEffect(() => {
        if (!isOpen) return undefined;
        let cancelled = false;
        getChatHistory()
            .then((history) => {
                if (cancelled) return;
                setHistoryError(null);
                setMessages(history.length
                    ? history.map((m) => ({ role: m.role, content: m.response }))
                    : [{ role: 'assistant', content: WELCOME }]);
            })
            .catch((error) => !cancelled && setHistoryError(error.message));
        return () => { cancelled = true; };
    }, [isOpen]);

    useEffect(() => {
        bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' });
    }, [messages, isOpen]);

    const handleSend = async (e) => {
        e.preventDefault();
        const text = input.trim();
        if (!text || loading) return;
        setInput('');
        setMessages((prev) => [...prev, { role: 'user', content: text }]);
        setLoading(true);
        try {
            const result = await chatWithAI(text);
            setMessages((prev) => [...prev, { role: 'assistant', content: result.response }]);
        } catch (error) {
            // Shown as an error, not as an assistant reply, and not stored by the backend.
            setMessages((prev) => [...prev, { role: 'error', content: error.message }]);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="position-fixed bottom-0 end-0 m-4" style={{ zIndex: 1050 }}>
            {!isOpen && (
                <button onClick={() => setIsOpen(true)} aria-label="Open chat"
                    className="btn btn-primary rounded-circle d-flex align-items-center justify-content-center"
                    style={{ width: 52, height: 52, boxShadow: 'var(--shadow-md)' }}>
                    <MessageCircle size={22} />
                </button>
            )}

            {isOpen && (
                <div className="kai-card overflow-hidden" style={{ width: 380, height: 560, display: 'flex', flexDirection: 'column', boxShadow: 'var(--shadow-md)' }} data-testid="chat-window">
                    <div className="kai-card-head">
                        <div className="d-flex align-items-center gap-2">
                            <Bot size={18} style={{ color: 'var(--accent)' }} />
                            <h2 className="kai-card-title fs-6">Kai assistant</h2>
                        </div>
                        <button onClick={() => setIsOpen(false)} aria-label="Close chat" className="kai-iconbtn" style={{ width: 30, height: 30 }}><X size={16} /></button>
                    </div>

                    <div className="p-3 overflow-auto custom-scrollbar flex-grow-1" style={{ background: 'var(--surface-2)' }}>
                        {historyError && <div className="kai-alert warn mb-3">Could not load chat history: {historyError}</div>}
                        {messages.map((msg, idx) => (
                            <div key={idx} className={`d-flex mb-3 ${msg.role === 'user' ? 'justify-content-end' : 'justify-content-start'}`}>
                                {msg.role !== 'user' && (
                                    <div className="flex-shrink-0 me-2 mt-1"><Bot size={18} className={msg.role === 'error' ? 'text-danger' : 'text-primary'} /></div>
                                )}
                                <div className="text-break"
                                    style={{
                                        maxWidth: '85%', fontSize: '0.875rem', padding: '10px 14px', borderRadius: 12,
                                        ...(msg.role === 'user'
                                            ? { background: 'var(--accent)', color: 'var(--surface)' }
                                            : msg.role === 'error'
                                                ? { background: 'var(--neg-bg)', color: 'var(--neg)', border: '1px solid var(--neg-border)' }
                                                : { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }),
                                    }}>
                                    {msg.role === 'assistant' ? <ReactMarkdown components={markdownComponents}>{msg.content}</ReactMarkdown> : msg.content}
                                </div>
                                {msg.role === 'user' && <div className="flex-shrink-0 ms-2 mt-1"><User size={18} className="text-secondary" /></div>}
                            </div>
                        ))}
                        {loading && <div className="text-muted small">Thinking…</div>}
                        <div ref={bottomRef} />
                    </div>

                    <div className="p-3" style={{ borderTop: '1px solid var(--border)', background: 'var(--surface)' }}>
                        <form onSubmit={handleSend} className="d-flex gap-2">
                            <input type="text" className="form-control" aria-label="Message"
                                placeholder="Ask about investing…" value={input} onChange={(e) => setInput(e.target.value)} disabled={loading} />
                            <button type="submit" className="btn btn-primary d-flex align-items-center justify-content-center p-0"
                                style={{ width: 40, height: 40 }} disabled={loading || !input.trim()} aria-label="Send">
                                <Send size={18} />
                            </button>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
};

export default ChatWidget;

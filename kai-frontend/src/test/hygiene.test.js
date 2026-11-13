import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

// vitest runs from the project root
const SRC = path.resolve(process.cwd(), 'src');

const sourceFiles = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'test' ? [] : sourceFiles(full);
    return /\.(jsx?|scss|css)$/.test(entry.name) ? [full] : [];
});

const files = sourceFiles(SRC).map((f) => ({ file: path.relative(SRC, f), code: fs.readFileSync(f, 'utf8') }));

const FORBIDDEN = [
    [/Math\.random\(/, 'random data'],
    [/\bmock/i, 'a mock data source'],
    [/simulate(ToolCall|Decision|Mandate)/i, 'a simulated action'],
    [/kai_token|kai_user|Authorization|useAuth|AuthContext|AuthProvider|Bearer/, 'authentication'],
    [/user_id|userId/, 'a user id'],
    [/ProtectedRoute|\/login|\/register/, 'a route guard'],
    [/growth_rate|\beps\b\s*:|price:\s*\d/, 'user-supplied valuation inputs'],
    [/4,783|15,055|37,695/, 'the old hardcoded index values'],
    [/Fed Chair Powell|OpenAI releases|Apple faces unexpected/, 'hardcoded headlines'],
];

describe('the frontend has no random, mock or auth code (FIXES.md B8, auth removed)', () => {
    it('scans every source file', () => {
        expect(files.length).toBeGreaterThan(20);
    });

    for (const [pattern, what] of FORBIDDEN) {
        it(`contains no ${what}`, () => {
            const hits = files.filter((f) => pattern.test(f.code)).map((f) => f.file);
            expect(hits).toEqual([]);
        });
    }

    it('has no leftover auth files', () => {
        for (const gone of ['context/AuthContext.jsx', 'features/auth/Login.jsx', 'features/auth/Register.jsx']) {
            expect(fs.existsSync(path.join(SRC, gone))).toBe(false);
        }
    });
});

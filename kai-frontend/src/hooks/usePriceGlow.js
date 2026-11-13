import { useEffect, useState } from 'react';

const FLASH_MS = 1200;

/** CSS class that flashes green/red for a moment when the price moves up/down. */
export const usePriceGlow = (currentPrice) => {
    // Previous price is state (not a ref) and is compared during render, so no state is set
    // synchronously inside an effect.
    const [prev, setPrev] = useState(currentPrice);
    const [glowClass, setGlowClass] = useState('');

    if (currentPrice !== prev) {
        setPrev(currentPrice);
        if (currentPrice !== null && currentPrice !== undefined && prev !== null && prev !== undefined) {
            if (currentPrice > prev) setGlowClass('glow-green-flash');
            else if (currentPrice < prev) setGlowClass('glow-red-flash');
        }
    }

    useEffect(() => {
        if (!glowClass) return undefined;
        const timer = setTimeout(() => setGlowClass(''), FLASH_MS);
        return () => clearTimeout(timer);
    }, [glowClass, currentPrice]);

    return glowClass;
};

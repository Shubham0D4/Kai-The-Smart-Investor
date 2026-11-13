import React from 'react';

/** A small tinted status label. tone: neutral | pos | neg | warn | info | accent. */
const Chip = ({ tone = 'neutral', mono = false, dot = null, className = '', children, ...rest }) => (
    <span className={`kai-chip ${tone}${mono ? ' mono' : ''}${className ? ` ${className}` : ''}`} {...rest}>
        {dot && <span className={`kai-dot ${dot}`} />}
        {children}
    </span>
);

export default Chip;

import { createContext, useContext } from 'react';

// The context object and hook live apart from the provider component so that component files
// export only components (Vite fast refresh).
export const CommitteeContext = createContext(null);

export const useCommittee = () => {
    const value = useContext(CommitteeContext);
    if (!value) throw new Error('useCommittee must be used inside <CommitteeProvider>');
    return value;
};

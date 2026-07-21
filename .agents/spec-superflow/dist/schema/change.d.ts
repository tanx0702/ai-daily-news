import { Requirement } from './base.js';
export type DeltaOperationType = 'ADDED' | 'MODIFIED' | 'REMOVED' | 'RENAMED';
export interface Rename {
    from: string;
    to: string;
}
export interface Delta {
    spec: string;
    operation: DeltaOperationType;
    description: string;
    requirement?: Requirement;
    requirements?: Requirement[];
    rename?: Rename;
}
export interface Change {
    name: string;
    why: string;
    whatChanges: string;
    deltas: Delta[];
    metadata?: {
        version?: string;
        format?: string;
        sourcePath?: string;
    };
}

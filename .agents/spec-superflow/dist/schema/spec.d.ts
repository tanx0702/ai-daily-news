import { Requirement } from './base.js';
export interface Spec {
    name: string;
    overview: string;
    requirements: Requirement[];
    metadata?: {
        version?: string;
        format?: string;
        sourcePath?: string;
    };
}

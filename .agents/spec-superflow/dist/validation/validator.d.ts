import { ValidationReport } from './types.js';
import type { VerificationReport, ConflictReport } from './types.js';
export declare class Validator {
    private strictMode;
    constructor(strictMode?: boolean);
    validateSpecContent(specName: string, content: string): ValidationReport;
    validateChangeContent(changeName: string, content: string): ValidationReport;
    validateDeltaSpec(content: string): ValidationReport;
    validateImplementation(diffSummary: string, specContent: string, designContent: string, config?: {
        verification?: {
            language?: string;
        };
    }): VerificationReport;
    private extractRequirementNames;
    private extractDecisionNames;
    detectSyncConflicts(deltaSpecs: Array<{
        changeName: string;
        content: string;
    }>): ConflictReport;
    isValid(report: ValidationReport): boolean;
}

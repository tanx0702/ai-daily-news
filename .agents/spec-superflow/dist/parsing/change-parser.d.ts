export interface ParsedDelta {
    spec: string;
    operation: string;
    description: string;
}
export interface ParsedChange {
    name: string;
    why: string;
    whatChanges: string;
    deltas: ParsedDelta[];
}
export declare function parseChangeMarkdown(content: string, changeName: string): ParsedChange;

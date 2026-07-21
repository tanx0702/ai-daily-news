export interface Scenario {
    rawText: string;
}
export interface Requirement {
    text: string;
    scenarios: Scenario[];
}

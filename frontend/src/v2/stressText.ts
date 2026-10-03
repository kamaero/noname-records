/** Remove combining acute accents (stress marks) from a word. */
export function stripStress(value: string): string {
  return (value || "").replace(/\u0301/g, "");
}

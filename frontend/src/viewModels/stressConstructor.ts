const VOWELS = new Set("аеёиоуыэюяАЕЁИОУЫЭЮЯ".split(""));
const STRESS = "́";

export type LetterSquare = { ch: string; index: number; isVowel: boolean };

export function splitLetters(word: string): LetterSquare[] {
  const clean = (word || "").replace(new RegExp(STRESS, "g"), "");
  return Array.from(clean).map((ch, index) => ({ ch, index, isVowel: VOWELS.has(ch) }));
}

export function placeStress(word: string, letterIndex: number): string {
  const squares = splitLetters(word);
  if (letterIndex < 0 || letterIndex >= squares.length || !squares[letterIndex].isVowel) {
    return squares.map((s) => s.ch).join("");
  }
  return squares.map((s, i) => (i === letterIndex ? s.ch + STRESS : s.ch)).join("");
}

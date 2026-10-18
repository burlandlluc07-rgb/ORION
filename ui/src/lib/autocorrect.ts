/**
 * Light auto-correct for the chat composer.
 *
 * Only fixes the word you just finished (when you type a space or punctuation after it), only from a list of
 * well-known typos, and never touches code, links, paths, emails, commands, numbers, ALLCAPS or MixedCase words.
 * Backspace or Ctrl+Z right after a fix puts your word back, and ORION then leaves that word alone.
 */

// Common English typos → fix. Lowercase keys; case is carried over from what you typed.
const TYPOS: Record<string, string> = {
  abotu: "about", acn: "can", accomodate: "accommodate", accross: "across", acheive: "achieve",
  acutally: "actually", adn: "and", adress: "address", agian: "again", agin: "again", alot: "a lot", alraedy: "already",
  alreayd: "already", alwasy: "always", alwyas: "always", amke: "make", anohter: "another", anwser: "answer", anyhting: "anything",
  apparantly: "apparently", appearence: "appearance", aroudn: "around", arguement: "argument", artical: "article",
  aswell: "as well", atleast: "at least", awsome: "awesome", basicly: "basically", becasue: "because", becuase: "because",
  becuse: "because", beacuse: "because", befoer: "before", beggining: "beginning", beleive: "believe", belive: "believe",
  bewteen: "between", brwoser: "browser", buisness: "business", busness: "business", calender: "calendar", cant: "can't",
  certian: "certain", chnage: "change", cheif: "chief", collegue: "colleague", comming: "coming", commited: "committed",
  completly: "completely", concious: "conscious", copmuter: "computer", coudl: "could", couldnt: "couldn't", definately: "definitely",
  definatly: "definitely", definitly: "definitely", diffrent: "different", differnt: "different", didnt: "didn't", dissapear: "disappear",
  dissapoint: "disappoint", doens: "does", doesnt: "doesn't", doign: "doing", dont: "don't", donwload: "download", dowload: "download",
  embarass: "embarrass", enviroment: "environment", eveyr: "every", everytime: "every time", exapmle: "example", excercise: "exercise",
  existance: "existence", experiance: "experience", explaination: "explanation", familar: "familiar", finaly: "finally", foriegn: "foreign",
  foward: "forward", freind: "friend", firend: "friend", frist: "first", fomr: "from", futher: "further",
  goign: "going", gonig: "going", goverment: "government", grammer: "grammar", gaurd: "guard", guage: "gauge", hadnt: "hadn't",
  happend: "happened", harrass: "harass", hasnt: "hasn't", havent: "haven't", hda: "had", helo: "hello", hepl: "help",
  heirarchy: "hierarchy", hte: "the", htis: "this", hwat: "what", hwo: "how", humourous: "humorous", ignorence: "ignorance",
  imediately: "immediately", immediatly: "immediately", independant: "independent", infomation: "information", informatoin: "information",
  intresting: "interesting", interesitng: "interesting", isnt: "isn't", jsut: "just", knwo: "know", konw: "know", knowlege: "knowledge",
  liek: "like", likley: "likely", libary: "library", lisence: "license", litle: "little", maintainance: "maintenance", mkae: "make",
  maek: "make", meesage: "message", mesage: "message", messsage: "message", millenium: "millennium", mispell: "misspell",
  mroe: "more", mabye: "maybe", maybee: "maybe", mustnt: "mustn't", neccessary: "necessary", necessery: "necessary", nedd: "need",
  noticable: "noticeable", nto: "not", nwo: "now", occassion: "occasion", occured: "occurred", occurence: "occurrence", ofr: "for",
  omre: "more", ont: "not", oen: "one", opne: "open", oppurtunity: "opportunity", otehr: "other", ohter: "other", peice: "piece",
  peopel: "people", poeple: "people", persistant: "persistent", posible: "possible", possable: "possible", potatos: "potatoes",
  prefered: "preferred", probaly: "probably", probabl: "probably", probly: "probably", proabbly: "probably", problme: "problem",
  publically: "publicly", pwoer: "power", quesiton: "question", questoin: "question", reccomend: "recommend", recomend: "recommend",
  recieve: "receive", recieved: "received", rember: "remember", remeber: "remember", relevent: "relevant", resturant: "restaurant",
  rythm: "rhythm", sayign: "saying", seach: "search", serach: "search", seperate: "separate",
  seperately: "separately", shoudl: "should", shouldnt: "shouldn't", sicne: "since", simliar: "similar",
  similiar: "similar", soem: "some", somehting: "something", somthing: "something", soemthing: "something", sometiems: "sometimes",
  speach: "speech", sucess: "success", succesful: "successful", successfull: "successful", suprise: "surprise", surley: "surely",
  taht: "that", tahn: "than", teh: "the", tehy: "they", thansk: "thanks", thnaks: "thanks", thakns: "thanks",
  thats: "that's", theri: "their", thier: "their", theres: "there's", thign: "thing", thigns: "things", thikn: "think", thnig: "thing",
  thnk: "think", threshhold: "threshold", throught: "through", tihs: "this", tiem: "time", tje: "the", tomorow: "tomorrow",
  tommorow: "tomorrow", tommorrow: "tomorrow", truely: "truly", tyhe: "the", tyring: "trying", udpate: "update",
  unitl: "until", untill: "until", useing: "using", usualy: "usually", veyr: "very", vrey: "very", wahat: "what", wasnt: "wasn't",
  watn: "want", wnat: "want", waht: "what", wehn: "when", wether: "whether", wierd: "weird",
  whcih: "which", wich: "which", whihc: "which", whta: "what", whats: "what's", wiht: "with", wih: "with", wiull: "will", wil: "will",
  withotu: "without", wokr: "work", wont: "won't", woudl: "would", wouldnt: "wouldn't", wriet: "write", writting: "writing",
  yeterday: "yesterday", yesterady: "yesterday", yoru: "your", yuor: "your", youre: "you're", yuo: "you",
  im: "I'm", ive: "I've", arent: "aren't", werent: "weren't", shes: "she's", theyre: "they're", weve: "we've",
  youve: "you've", theyve: "they've", wouldve: "would've", couldve: "could've", shouldve: "should've",
};

const BOUNDARY = /[\s.,!?;:)\]"]/;

export interface Correction {
  start: number; // where the fixed word starts in the text
  original: string; // what you typed
  fixed: string; // what ORION put there
}

/** Words you undid a fix for this session: ORION leaves them alone from then on. */
const kept = new Set<string>(JSON.parse(sessionStorage.getItem("orion.ac.keep") ?? "[]"));

export function keepWord(word: string) {
  kept.add(word.toLowerCase());
  try { sessionStorage.setItem("orion.ac.keep", JSON.stringify([...kept])); } catch { /* private mode */ }
}

function matchCase(typed: string, fix: string): string {
  if (fix.startsWith("I'") || fix === "I") return fix; // "im" → "I'm" whatever the case
  if (typed[0] === typed[0].toUpperCase()) return fix[0].toUpperCase() + fix.slice(1);
  return fix;
}

/** True if the caret sits inside `inline code` or a ``` block. */
function inCode(before: string): boolean {
  if ((before.split("```").length - 1) % 2 === 1) return true;
  if (((before.replace(/```/g, "").match(/`/g) ?? []).length) % 2 === 1) return true;
  const line = before.slice(before.lastIndexOf("\n") + 1);
  return /^( {4}|\t|\s*\$ )/.test(line); // indented code or a "$ command" line
}

/**
 * Called after each edit. If you just typed one boundary character after a known typo, returns the corrected
 * text and what changed; otherwise null.
 */
export function autocorrect(prev: string, next: string, caret: number): { text: string; fix: Correction } | null {
  if (next.length !== prev.length + 1 || caret < 2) return null;
  if (next.slice(0, caret - 1) + next.slice(caret) !== prev) return null; // must be a single typed character
  if (!BOUNDARY.test(next[caret - 1])) return null;
  const before = next.slice(0, caret - 1);
  // The whole chunk since the last space: if it's anything but letters and apostrophes (a link, path, email,
  // @mention, #tag, flag, number, snake_case, file.ext…) leave it be.
  const chunk = /(\S*)$/.exec(before)![1].replace(/^[("'[]+/, "");
  if (!/^[A-Za-z][A-Za-z']*$/.test(chunk)) return null;
  const word = chunk;
  const start = before.length - word.length;
  if (start === 0 && next.startsWith("/")) return null;
  if (/^\/\S*$/.test(before.slice(0, start).trim())) return null; // first word after a /skill
  if (word.length > 1 && word !== word.toLowerCase() && word.slice(1) !== word.slice(1).toLowerCase()) return null; // ALLCAPS, camelCase
  // A capital letter mid-sentence is usually a name: leave it.
  if (word[0] !== word[0].toLowerCase() && !/(^|[.!?\n]\s*)$/.test(before.slice(0, start).replace(/["'([]+$/, ""))) return null;
  if (kept.has(word.toLowerCase()) || inCode(before)) return null;
  let fix: string | undefined;
  if (word === "i") fix = "I";
  else fix = TYPOS[word.toLowerCase()];
  if (!fix) return null;
  fix = matchCase(word, fix);
  if (fix === word) return null;
  return { text: next.slice(0, start) + fix + next.slice(before.length), fix: { start, original: word, fixed: fix } };
}

/** Put the original word back. Returns null if the text around the fix has changed since. */
export function undoFix(text: string, fix: Correction): string | null {
  if (text.slice(fix.start, fix.start + fix.fixed.length) !== fix.fixed) return null;
  return text.slice(0, fix.start) + fix.original + text.slice(fix.start + fix.fixed.length);
}

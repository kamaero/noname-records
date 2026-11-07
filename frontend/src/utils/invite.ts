import { roleLabel } from "../v2/types";
/** Текст приглашения в бота — один на «Дикторы» и каст. С ролью и книгой — когда зовём
 *  на конкретную роль того, кто ещё не на связи. Имя студии — из настроек установки. */
export function inviteText(bot: string, studio: string, role?: { role: string; book: string }): string {
  const about = role ? `You are invited to voice «${roleLabel(role.role)}» in the book «${role.book}».\n\n` : "";
  const name = studio.trim() || "Noname Records";
  return (
    `Hello from the voice recording studio «${name}».\n\n` +
    about +
    "Our workspace has your roles, scripts and recording uploads. Open our bot and press Start:\n" +
    `https://t.me/${bot}?start=dictor\n\n` +
    "The bot will confirm that it can reach you and send a link to the site. Sign in with Telegram; no password is needed. " +
    "If Telegram sign-in does not work, press the bot's 🔑 Username and password button.\n\n" +
    "The bot will also send casting confirmations and lines to record again."
  );
}

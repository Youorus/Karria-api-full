# ADR-0031 — Envoi des documents par e-mail : pièces jointes, pas de texte libre, cinq envois par jour, remise en file

**Statut :** Proposée — 2026-11-05 (écrite par Fable, à valider par Marc)
**Complète :** ADR-0020, ADR-0030

## Le problème

Une personne veut recevoir son CV sur son ordinateur, ou l'envoyer à
quelqu'un. Karria envoie déjà des e-mails (codes, bienvenue), mais jamais de
pièce jointe, jamais à une adresse choisie par la personne. Un envoi vers une
adresse libre fait de Karria un relais de courrier : il faut qu'il soit
**borné** et **impossible à détourner**.

## La décision

1. **Deux routes de session** : `POST /documents/exports/{id}/send` et
   `POST /documents/exports/send` (`generated_ids` : plusieurs documents
   **dans un seul e-mail**, CV + lettre). `to: null` vaut l'adresse du compte,
   lue par le port `AccountDirectory` branché sur `identity` (règle 4).
2. **Aucun texte libre.** Le corps est un gabarit du centre d'e-mails
   (`documents_sent`) : « Prénom Nom vous transmet ses documents de
   candidature », la liste des pièces, et la **signature au nom de la
   personne** (pas « L'équipe Karria »). Le sujet est fixé par le registre.
   Rien de ce que tape la personne n'entre dans le message : c'est ce qui
   empêche d'en faire un outil de spam.
3. **Pièces jointes au port mail** (`OutgoingMail.attachments`,
   `Attachment(file_name, content_type, data)`) : le socle les porte, SMTP les
   compose (`add_attachment`), la console les note. Le PDF est joint quand il
   existe, sinon le DOCX ; les noms sont ceux du téléchargement (« CV - Prénom
   Nom.pdf », « Lettre de motivation - Entreprise.pdf »).
4. **Cinq envois par jour civil (UTC) et par compte** — règle du domaine
   (`DAILY_SEND_LIMIT`), comptée sur le **journal des envois**
   (`documents_sends`, migration 17) : `429 send_quota_exceeded`,
   `details.retry_after` jusqu'à minuit. Adresse validée (forme, longueur,
   domaine avec un point) : `422 recipient_invalid`. Un document qui n'est pas
   à la personne : `404 generated_cv_not_found`.
5. **L'API répond `202` avec l'envoi en état `queued`** ; la remise effective
   est un travail de file (`documents.send_documents`, procrastinate, un
   verrou par envoi) qui lit les fichiers du bucket, compose et remet, puis
   marque `sent` ou `failed` (raison technique dans le journal, jamais dans la
   réponse). `GET /documents/sends/{id}` relit l'état.

## Conséquences

- Le quota compte les envois **demandés** (créés), pas les remis : un envoi
  en échec consomme sa place, sinon une adresse injoignable permettrait des
  tentatives sans fin.
- Le journal garde l'adresse de destination : c'est une donnée personnelle,
  effacée avec le compte (`ForgetUser`).
- Le gabarit existe en français et en anglais (règle du registre) ; la langue
  suit le compte quand elle est connue, sinon le français.

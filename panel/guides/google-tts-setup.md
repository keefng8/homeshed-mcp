---
title: Google text to speech
slug: google-tts-setup
audience: owner
updated: 2026-10-28
---

# Google text to speech

## What it is
HomeShed can turn text into spoken MP3 files with Google's best voices (called **Chirp 3 HD**). It's made for
audiobooks: an app sends a book a piece at a time, and HomeShed sends back the audio.

Google gives you **1 million characters a month free** (a character is one letter, space or punctuation mark; a
700,000-character book fits). After that Google charges **$30 per million**. HomeShed stops at the free million
unless you tell it otherwise, so it won't cost you anything by surprise.

You do the setup once. It takes about 15 minutes; if you've already done Steps 1 to 4, start at Step 5. Do the steps in order.

## Words used here
- **Google Cloud**: Google's service for developers. It's separate from your Gmail, but you sign in with the same
  Google account.
- **Project**: a folder in Google Cloud that holds one set of services and keys.
- **API**: a way for one program to ask another to do something. Here, HomeShed asks Google to speak text.
- **Service account**: a robot Google account that belongs to your project. HomeShed signs in as it.
- **Key file**: a small file Google gives you for the service account. It works like a password: keep it
  private.
- **Billing account**: the card Google charges if you go past the free amount.

## Step 1: Open Google Cloud
1. Go to **console.cloud.google.com**.
2. Sign in with your Google account.
3. If it asks you to agree to the terms, read them and agree.

## Step 2: Make a project
1. At the top of the page, click the project list (it may say **Select a project**).
2. Click **New project**.
3. Name it **HomeShed Speech** (any name works).
4. Click **Create**, then wait a few seconds.
5. Open the project list again and pick **HomeShed Speech**, so the next steps happen inside it.

## Step 3: Turn on billing
Google asks for a card **even for the free amount**: the free million is part of a paid account, and Google charges
the card only for what goes past it. HomeShed's own limit stops at the free million unless you change it (Step 7).

1. In the menu on the left (the three lines at the top left), click **Billing**.
2. Click **Link a billing account**, or **Create account** if you don't have one.
3. Follow Google's steps to add your card.
4. Optional but wise: in **Billing > Budgets & alerts**, create a budget of **£1** so Google emails you if anything
   is ever charged.

## Step 4: Switch on the Text-to-Speech API
1. In the menu, click **APIs & Services > Library**.
2. In the search box, type **Text-to-Speech**.
3. Click **Cloud Text-to-Speech API**.
4. Click **Enable**.

## Step 5: Make a service account and its key file
Google's text to speech doesn't take a plain API key (it answers "API keys are not supported by this API"). It wants a
service account, so HomeShed signs in as one.

1. In the menu, click **IAM & Admin > Service accounts**.
2. Click **Create service account**.
3. **Service account name**: type `homeshed-tts`. Click **Create and continue**.
4. **Permissions**: text to speech needs none, so click **Continue**, then **Done**.
5. In the list, click the new account (**homeshed-tts@...**).
6. Open the **Keys** tab. Click **Add key > Create new key**.
7. Choose **JSON** and click **Create**. A file downloads (its name ends in `.json`).

If Google says key creation is turned off for your organisation, tell me: that only happens on work or school
accounts, and there's another way.

## Step 6: Store the key file in HomeShed
1. Open the downloaded `.json` file with **Notepad** (right-click it, **Open with > Notepad**).
2. Press **Ctrl+A** to select it all, then **Ctrl+C** to copy.
3. Open the **Control Panel > Settings > Credentials**.
4. Click **Add a key**.
5. **What is it?**: type `Google text to speech service account`.
6. **Name**: type `GOOGLE_TTS_SERVICE_ACCOUNT` exactly, in capitals.
7. Paste into the value box (**Ctrl+V**). It's long; that's right.
8. Click **Save**.
9. Delete the downloaded `.json` file, and empty the Recycle Bin. HomeShed now keeps the only copy, encrypted.

Your AI sessions and apps use it through HomeShed's tools and never see it. Don't paste it anywhere else.

If you made an API key for this earlier, it isn't needed: you can delete it in Google (**APIs & Services >
Credentials**) and in HomeShed.

## Step 7 (optional): Allow paid characters
Leave this alone to stay free. If you ever want more than a million characters in a month:
1. Open **Control Panel > Settings > Text to speech**.
2. Pick an amount under **paid characters a month**. Each 100,000 characters is about $3.

**None** (the default) means free only.

## Step 8: Let the app use it
On **Control Panel > API access**, open the app that will make the audio (for example the station's app) and grant it
`tts.voices`, `tts.synthesize`, `tts.fetch` and `tts.usage`.

## Checking it works
Ask a session to run `tts.voices` with language `en-GB`. A list of voices means it's working. If it says the sign-in
was refused, check Step 4 (the API must be enabled in the same project as the service account) and Step 6 (the
whole file pasted).

## How much is left
`tts.usage` shows this month's characters, what's left and the date it starts again. Google's months run on US
Pacific time, so the count resets at about 8 a.m. UK time on the 1st. HomeShed only counts its own use: anything else
using the same Google account isn't included.

# Appointment Reminders

[![mini-automations-n8n](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml)

**Problem.** Clinics lose revenue to no-shows, and reception spends the afternoon calling tomorrow's patients one by one.
This workflow sends each patient a WhatsApp reminder the day before and 2 hours before, in English or Arabic, and records what was sent.

Type: single n8n workflow ([workflow.json](workflow.json)). Stack: n8n, Supabase, WhatsApp Cloud API (templates).

**Model:** none. Reminder timing is deterministic and the text is a pre-approved template, so an LLM would add cost and risk without improving anything.

## How it works

```
every 30 minutes  (or POST /webhook/appointment-reminders with Header Auth)
  -> Supabase: booked appointments starting in the next 24 hours
  -> per appointment:
       starts in 2 to 24 h and no day-before reminder yet  -> day-before reminder (not during quiet hours)
       starts within 2 h and no 2 h reminder yet            -> 2 h reminder
  -> send WhatsApp template in the patient's language
  -> mark reminder_24h_sent_at / reminder_2h_sent_at, only for sends that succeeded
```

Appointments booked less than 24 hours ahead still get a reminder on the next run. Day-before reminders wait out quiet hours (22:00 to 08:00 local); 2-hour reminders do not. A failed send is not marked, so the next run retries it.

## Setup

1. Run `supabase/appointments.sql` in the Supabase SQL editor.
2. In WhatsApp Manager, create a **Utility** template named `appointment_reminder` in English and Arabic, with five body variables (first name, doctor, date, time, location):
   - EN: `Hi {{1}}, a reminder of your appointment with {{2}} on {{3}} at {{4}} at {{5}}. To reschedule, call us or reply to this message.`
   - AR: `مرحباً {{1}}، نذكرك بموعدك مع {{2}} يوم {{3}} الساعة {{4}} في {{5}}. لإعادة الجدولة، اتصل بنا أو رد على هذه الرسالة.`
3. Import `workflow.json` in n8n and create credentials:
   - **Supabase**: Custom Auth with `{"headers": {"apikey": "<service role key>", "Authorization": "Bearer <service role key>"}}`.
   - **WhatsApp Cloud API**: Header Auth, name `Authorization`, value `Bearer <token>`.
   - **Reminders webhook token**: Header Auth, name `X-Webhook-Token`.
4. Edit the settings block in **Prepare run** (Supabase URL, phone number ID, template name, timezone, quiet hours), then publish.

## Dry run (no API keys)

POST sample appointments and a fixed clock. Nothing is read from Supabase and nothing is sent.

```bash
curl -s -X POST "http://localhost:5678/webhook/appointment-reminders?dry_run=1" \
  -H 'content-type: application/json' -H 'X-Webhook-Token: <token>' \
  -d @samples/dry_run_request.json
```

With the sample (8 synthetic appointments, clock at 09:00 Dubai time): 4 reminders due, 4 skipped (more than 24 hours away, two already reminded, one cancelled).

```
24h en  Hi Aisha, a reminder of your appointment with Dr. Lina Haddad on Wednesday 11 November at 08:30 ...
2h  ar  مرحباً فاطمة، نذكرك بموعدك مع د. سامي يوسف يوم الثلاثاء 10 نوفمبر الساعة 10:30 ...
```

## Tests

Runs on the shared test bench, see [../README.md](../README.md#test-bench).

```bash
python3 -m unittest discover -s appointment-reminders/tests -v
```

8 tests: due reminders and skip reasons, Arabic template payload and preview, quiet hours (day-before deferred, 2-hour still sent), webhook token required, dry run makes no calls, the live Supabase query, WhatsApp sends and PATCH updates, and that failed sends are not marked as sent.

## Limits

- Reminders only; replies ("reschedule") land in the normal WhatsApp inbox. Handling them is a separate workflow.
- One location string per appointment; add an Arabic location column if Arabic messages should name the clinic in Arabic.
- A 30-minute schedule means reminders go out up to 30 minutes after the exact 24 h or 2 h mark.

## Demo

[demo.gif placeholder: the dry run response, then the two WhatsApp reminders arriving on a phone]

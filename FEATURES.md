# Contract Addon Features

## Contract And Mobile Service Invoice-User Workflows

Allows users with invoice/accounting-user permissions to run contract and mobile service workflows without granting them broad manual delete access on contract lines.

### What Changed

- The mobile invoice settings action `Premazat nadspotreby v zmluvach` removes only the generated HLAS overconsumption contract lines with elevated rights.
- Moving a mobile service between inventories can replace the generated contract line even when the user cannot manually delete arbitrary contract lines.
- Reprocessing or resetting mobile invoice imports can clear generated import lines, and usage-report generation can replace old generated report attachments.

### Behavior

- General manual deletion of `contract.line` records remains restricted by the existing access rules.
- Elevated deletion is limited to controlled workflow methods that first select the records they generate or maintain.

## Automatic Customer Document Sender

Sends posted customer invoices and credit notes from the scheduled action while avoiding documents that are already sent or manually marked as sent.

### Behavior

- The scheduled sender excludes customer documents belonging to company ID `5` (`Oliver Brunovský`), so those invoices and credit notes are not claimed or emailed by the cron.
- Customer document emails include all attachments already linked to the invoice or credit note, such as Excel usage reports, in addition to the invoice PDF.

## Vzájomný zápočet pohľadávok a dobropisov

Pridáva účtovný workflow pre zákaznícke doklady: `VZÁJOMNÝ ZÁPOČET ZÁVÄZKOV A POHĽADÁVOK`.

### Čo sa zmenilo

- Pridaný model `account.customer.settlement` ako auditovateľný doklad zápočtu pre zaúčtované vydané faktúry a dobropisy.
- Pridaná akcia `Vytvoriť zápočet` v zozname a formulári vydaných faktúr a dobropisov.
- Potvrdenie vyrovná vybrané otvorené riadky pohľadávok natívnym čiastočným vyrovnaním v Odoo, započíta menšiu stranu a prípadný zostatok nechá otvorený na štandardné párovanie bankového výpisu.
- Potvrdenie vytvorí snímku pôvodnej sumy, započítanej sumy a zostatku pre každý zahrnutý doklad.
- Pridaná QWeb PDF zostava podľa dodanej šablóny zápočtu s blokmi dodávateľa/odberateľa, riadkami dokladov, súčtami a jasným textom, kto má uhradiť zostatok.
- Pridaná e-mailová šablóna a kontrolovaný proces odoslania: po potvrdení Odoo otvorí predvyplnený editor správy s priloženým PDF zápočtu.
- Pre zostatok sa nevytvára žiadna nová faktúra ani dobropis.

### Správanie

- Verzia 1 podporuje iba vydané faktúry a dobropisy.
- Jeden zápočet môže obsahovať viac faktúr a viac dobropisov pre rovnakého komerčného zákazníka, spoločnosť, menu a účet pohľadávok.
- Systém odmietne návrhy, zrušené alebo plne uhradené doklady, ako aj výbery s rôznymi partnermi, spoločnosťami, menami alebo účtami pohľadávok.
- Potvrdené zápočty táto funkcia neresetuje; opravy sa robia štandardnými účtovnými nástrojmi Odoo na zrušenie vyrovnania alebo účtovnú opravu.

## Monthly Customer Overpayment Report

Sends a monthly internal email report for customers with unresolved overpayments.

### What Changed

- Added an hourly cron that only runs the check on the 15th day of the month at 10:00 Europe/Bratislava time.
- The report includes only commercial customers whose combined overpayment exceeds 5 EUR.
- Overpayments can come from posted, unreconciled receivable lines with a negative residual amount, or from unreconciled incoming bank statement payments when they can be conservatively matched to a customer that has been invoiced before.
- Bank payments are matched only by an existing statement partner, exact invoice reference, unique registered IBAN, or unique historical IBAN pairing; ambiguous or unmatched payments are skipped.
- For each included customer, the email shows a readable summary table, while the attached Excel file gives each customer a separate sheet with all unresolved receivable lines and matched unreconciled bank payments.
- The report is sent to `tomas.juricek@novem.sk` and `oliver.brunovsky@novem.sk`, and no email is sent when no overpayments are found.

## Employee Timer

Adds a backend systray timer beside the switch-user icon. Employees can start the timer manually, pause or resume it without a ticket, clear it without logging time, or choose one of their open assigned `Starostlivosť o zákazníka` helpdesk tickets and save the result as a real timesheet line on the ticket's intervention task.

### What Changed

- Added `contract.timer.session` to persist running, paused, stopped, and discarded timer sessions per user.
- Added timer audit fields to `account.analytic.line`: timer session, raw hours, rounded hours, and 2x-rate flag; the selected ticket is retained on the timer session and through the linked intervention task.
- Added a helpdesk ticket smart button for timer-created timesheets.
- Timer-created timesheets are written to the selected ticket's linked Field Service task; if no task exists, the timer creates one using the same backend flow as the `Plan Intervention` button.
- Added backend systray JS/XML/SCSS assets for start, elapsed display, pause, resume, clear, ticket selection, and 2x-rate selection.
- The stop dialog requires a `Popis`; that text becomes the timesheet description instead of an automatic `Timer: ...` label.
- When a ticket has multiple intervention tasks, the stop dialog asks the employee which task should receive the time.
- Timer dialog labels, notifications, validation messages, and timer-related helpdesk/work-log labels are shown in Slovak.
- The systray timer uses a compact hourglass icon with a tight elapsed-time label and extra leading margin from the user switcher, keeping navbar icon spacing consistent while running or paused.
- Timer systray colors inherit the active Odoo navbar theme, so the icon and elapsed time remain readable in dark and white modes.
- The systray timer display is based on server elapsed time plus browser wall-clock delta, so background-tab throttling cannot make the visible timer drift by minutes.
- The stop dialog uses scoped modal overflow styles to avoid unnecessary horizontal or vertical scrollbar jitter.
- Added access rules so users can only operate on their own timer sessions, while helpdesk managers can inspect all sessions.

### Behavior

- One active timer is allowed per Odoo user; both running and paused timers count as active.
- Pausing stops the elapsed-time counter without requiring a ticket.
- Resuming continues the same timer and excludes the paused interval from the saved duration.
- Clearing a running or paused timer marks the session as discarded and creates no timesheet line.
- Saving timer time requires an active, non-folded customer-care ticket assigned to the current user.
- Saving timer time requires a user-written description.
- Saved timer time is linked to a `project.task`/úloha, while the timer session keeps the selected helpdesk ticket for audit.
- Raw elapsed time is rounded up to the nearest half hour.
- Any positive timer duration has a minimum rounded value of `0.5` hours.
- If `2x rate` is enabled, the saved timesheet `unit_amount` is doubled; raw and rounded values remain stored for audit.
- The selected helpdesk ticket receives a chatter note with raw, rounded, and billable hours.

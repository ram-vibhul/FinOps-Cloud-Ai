> **DRAFT — NOT REVIEWED BY A LAWYER. NOT FOR PUBLICATION.** Placeholders are in [SQUARE BRACKETS].

# CostLens Data Processing Agreement

Last updated: [DATE]

This Data Processing Agreement ("**DPA**") forms part of the CostLens Terms of Service
between the Customer and [YUGIVERSE LEGAL ENTITY NAME] ("**Yugiverse**").

## 1. Roles and scope

1.1 The Customer is the Data Fiduciary (DPDP Act, 2023) / controller (GDPR) of personal
data in Customer Data. Yugiverse is the Data Processor / processor and processes it only
to provide the Service and on the Customer's documented instructions, which are these
Terms, this DPA and the Customer's use of the product's settings.

1.2 **Processing details**

| | |
|---|---|
| Subject matter | Hosting and processing Customer Data to provide cost management |
| Duration | The term of the Terms plus the deletion period in section 8 |
| Nature and purpose | Collection from connected accounts, storage, analysis, display, notifications |
| Personal data | Names and emails of users; names, emails or ids that appear in resource names, tags, owners, audit and change records |
| Data subjects | The Customer's users and people named in its cloud metadata |
| Special categories | None intended; the Customer should not put them in tags or names |

## 2. Yugiverse's obligations

Yugiverse will:

(a) process personal data only on documented instructions, and tell the Customer if an
instruction appears to break the law;
(b) ensure people with access are bound by confidentiality;
(c) apply the security measures in Annex 1;
(d) use subprocessors only as in section 4;
(e) help the Customer respond to data-subject requests, mainly through the product's
export and deletion features;
(f) help with security, breach notification and impact assessments, taking into account
the information available to Yugiverse;
(g) delete or return personal data at the end of the Service as in section 8;
(h) make available information needed to show compliance, and allow audits as in section 7.

## 3. Personal data breaches

Yugiverse will notify the Customer without undue delay, and within [48] hours after
becoming aware of a personal data breach affecting Customer Data, with the information
available, and update it as more becomes known. [COUNSEL: align with DPDP Rules timelines
for the Customer's own notice to the Board.]

## 4. Subprocessors

4.1 The Customer authorises the subprocessors listed at [SUBPROCESSOR URL] (current list
in the Privacy Notice, section 3).

4.2 Yugiverse will give at least 30 days' notice of a new subprocessor. The Customer may
object on reasonable data-protection grounds; if no solution is agreed, the Customer may
terminate the affected Service and receive a refund of prepaid fees for the remaining
term.

4.3 Yugiverse imposes data-protection terms on each subprocessor at least as protective
as this DPA and remains responsible for them.

## 5. International transfers

[COUNSEL: DPDP Act section 16; GDPR Standard Contractual Clauses (module 2/3) attached as
Annex 2 for EU/UK Customers; hosting regions — Q7.]

## 6. Customer obligations

The Customer is responsible for the lawfulness of the data it connects, for giving
notices and obtaining consents where required, and for the roles and access it grants.

## 7. Audits

Yugiverse will answer reasonable security questionnaires once a year and provide summaries
of independent assessments when available. On-site audits: [COUNSEL — notice, frequency,
cost, confidentiality].

## 8. Deletion

When an organisation is deleted in the product, its Customer Data is deleted from the
production database [immediately / within 30 days] and from backups within [35] days as
backups expire. The Customer can export data before deleting.

## Annex 1 — Security measures

- **Access to customer clouds:** read-only roles created by the Customer; AWS roles use a
  per-connection external id; write access only with a separate opt-in role and approval
  for each change.
- **Tenant isolation:** row-level security in PostgreSQL on every tenant table, plus
  checks in application code.
- **Encryption:** in transit with TLS; secrets (keys, webhook addresses, service principal
  secrets) encrypted at rest with rotatable keys; [DATABASE/DISK ENCRYPTION AT REST BY THE
  HOSTING PROVIDER — Q7].
- **Access control:** single sign-on, enforced SSO for verified domains, SCIM, roles and
  custom roles, temporary elevated access with expiry.
- **Logging:** an audit log of changes, exportable by the Customer; secrets redacted in logs.
- **Data minimisation and retention:** retention settings per organisation with automatic
  deletion; no card data stored.
- **Software supply chain:** signed release images with SBOM and provenance; dependency
  licence checks; automated tests including tenant isolation on PostgreSQL.
- **Staff access:** [TO BE DESCRIBED — who at Yugiverse can access production, how
  access is logged].

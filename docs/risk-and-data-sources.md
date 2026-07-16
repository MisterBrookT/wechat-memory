# Risk and external data sources

This document is factual project context, not legal advice.

## Project boundary

WeChat Memory starts after data has been lawfully obtained and converted to the public JSON contract. This repository does not provide, copy, call, or document methods for:

- extracting encryption keys;
- decrypting protected databases;
- scanning process memory;
- reverse engineering proprietary formats;
- Hook/injection or client modification;
- bypassing access controls or technical protection measures.

The project does not accept issues or pull requests requesting those capabilities.

## Platform and legal risk

Tencent's [policy index](https://www.tencent.com/zh-cn/policies/) links the current Weixin software license and privacy policy. The license includes restrictions concerning unauthorized control, access, retrieval, interference, and circumvention involving software data and protection measures.

On 2026-07-15, GitHub blocked the `jackwener/wx-cli` repository after a DMCA notice. The published notice alleges unlawful circumvention and also raises claims about protected database design and platform terms. Read the [GitHub DMCA notice](https://github.com/github/dmca/blob/master/2026/07/2026-07-13-wechat.md) directly.

A disclaimer or a user's possession of a local copy does not itself resolve copyright, anti-circumvention, contract, privacy, personal-information, or local-law questions.

## Independent community projects

Links are supplied for ecosystem context only. They are not dependencies, mirrors, endorsements, or instructions. Their availability, licenses, and legal status may change.

- [`jackwener/wx-cli`](https://github.com/jackwener/wx-cli) — unavailable through the GitHub API as of 2026-07-16 following the notice above.
- [`ILoveBingLu/CipherTalk`](https://github.com/ILoveBingLu/CipherTalk) — independent local chat/AI project; review its current license and risk before use.
- [`BlueMatthew/WechatExporter`](https://github.com/BlueMatthew/WechatExporter) — independent iOS-backup exporter, GPL-2.0 at the time checked.

No code from these projects is included here.

## Personal information

Chat data can contain identifiers, contact details, location, financial information, health information, minors' information, and statements made by other people. Operators should implement:

- lawful purpose and data minimization;
- appropriate notice or consent where required;
- local-only processing by default;
- retention and deletion controls;
- strict access permissions and encrypted backups;
- review before sending evidence to any cloud model;
- correction paths for inaccurate AI-derived profiles.

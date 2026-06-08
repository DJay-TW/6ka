import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const baseDir = "C:/Users/88698/Documents/6KA系統開發/outputs/linepay_may_2026";
const detailCsv = path.join(baseDir, "linepay_2026-05_details.csv");
const dailyCsv = path.join(baseDir, "linepay_2026-05_daily_summary.csv");
const metaPath = path.join(baseDir, "linepay_2026-05_meta.json");
const outputPath = path.join(baseDir, "6KA_LinePay_2026-05_reconciliation.xlsx");

function parseCsv(text) {
  const lines = text.replace(/^\uFEFF/, "").trimEnd().split(/\r?\n/);
  const rows = [];
  for (const line of lines) {
    const row = [];
    let cell = "";
    let quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (ch === '"') {
        if (quoted && line[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          quoted = !quoted;
        }
      } else if (ch === "," && !quoted) {
        row.push(cell);
        cell = "";
      } else {
        cell += ch;
      }
    }
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

function numberizeRows(rows, numericNames) {
  const headers = rows[0];
  const numericIndexes = headers
    .map((name, idx) => (numericNames.has(name) ? idx : -1))
    .filter(idx => idx >= 0);
  return rows.map((row, rowIdx) => {
    if (rowIdx === 0) return row;
    return row.map((value, idx) => {
      if (!numericIndexes.includes(idx) || value === "") return value;
      const num = Number(value);
      return Number.isFinite(num) ? num : value;
    });
  });
}

function setWidths(sheet, widths) {
  widths.forEach((width, idx) => {
    sheet.getRangeByIndexes(0, idx, 1, 1).format.columnWidthPx = width;
  });
}

function styleHeader(range) {
  range.format = {
    fill: "#214E5A",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
  };
}

const details = numberizeRows(parseCsv(await fs.readFile(detailCsv, "utf8")), new Set([
  "order_status",
  "order_total_amount",
  "payment_type_code",
  "gross_amount",
  "redeem_amount",
  "change_amount",
  "net_amount",
]));
const daily = numberizeRows(parseCsv(await fs.readFile(dailyCsv, "utf8")), new Set([
  "transaction_count",
  "gross_amount",
  "redeem_amount",
  "change_amount",
  "net_amount",
]));
const meta = JSON.parse(await fs.readFile(metaPath, "utf8"));
const total = meta.total;

const workbook = Workbook.create();
const summary = workbook.worksheets.add("摘要");
const dailySheet = workbook.worksheets.add("日彙總");
const detailSheet = workbook.worksheets.add("LinePay明細");
const notes = workbook.worksheets.add("資料欄位說明");

summary.showGridLines = false;
summary.getRange("A1:H1").merge();
summary.getRange("A1").values = [["6KA LinePay 2026年5月對帳資料"]];
summary.getRange("A1").format = {
  fill: "#214E5A",
  font: { bold: true, color: "#FFFFFF", size: 16 },
};
summary.getRange("A3:B10").values = [
  ["期間", `${total.period_start} ~ ${total.period_end}`],
  ["LinePay 總筆數", total.linepay_transaction_count],
  ["LinePay 總金額", total.linepay_gross_amount],
  ["找零/沖抵金額", total.linepay_change_amount],
  ["淨收金額", total.linepay_net_amount],
  ["第一筆訂單時間", total.first_order_time],
  ["最後一筆訂單時間", total.last_order_time],
  ["資料來源", meta.source_db],
];
summary.getRange("A3:A10").format = { fill: "#E7F0F2", font: { bold: true } };
summary.getRange("B5:B7").format.numberFormat = "#,##0";
summary.getRange("B4").format.numberFormat = "#,##0";
summary.getRange("A12:H12").merge();
summary.getRange("A12").values = [["重要說明"]];
summary.getRange("A12").format = { fill: "#F1F5F9", font: { bold: true } };
summary.getRange("A13:H16").values = [
  ["本檔案為 6KA 本機 sales_cache.sqlite 內，付款類型名稱包含 Line 的 2026-05-01 至 2026-05-31 交易。", "", "", "", "", "", "", ""],
  ["金額欄位包含 gross_amount、change_amount、net_amount；本期間 LinePay change_amount 為 0，因此 gross/net 相同。", "", "", "", "", "", "", ""],
  ["本機 DB 未保存 LINE Pay gateway 交易序號、授權碼、回應碼、settlement id 或網路連線 log。", "", "", "", "", "", "", ""],
  ["可提供給 LINE 公司核對的主要鍵值：order_id、display_id、order_guid、payment_guid、payment_timestamp、amount。", "", "", "", "", "", "", ""],
];
for (const row of [13, 14, 15, 16]) {
  summary.getRange(`A${row}:H${row}`).merge();
  summary.getRange(`A${row}:H${row}`).format.rowHeightPx = 34;
}
summary.getRange("A13:H16").format = { wrapText: true };
summary.getRange("D3:E6").values = [
  ["核對項目", "數值"],
  ["明細筆數", "=COUNTA('LinePay明細'!A2:A701)"],
  ["明細總額", "=SUM('LinePay明細'!Q2:Q701)"],
  ["日彙總總額", "=SUM('日彙總'!F2:F32)"],
];
styleHeader(summary.getRange("D3:E3"));
summary.getRange("E4:E6").format.numberFormat = "#,##0";
setWidths(summary, [160, 360, 24, 140, 140, 24, 140, 140]);

dailySheet.getRangeByIndexes(0, 0, daily.length, daily[0].length).values = daily;
styleHeader(dailySheet.getRangeByIndexes(0, 0, 1, daily[0].length));
dailySheet.tables.add(`A1:H${daily.length}`, true, "LinePayDailySummary");
dailySheet.freezePanes.freezeRows(1);
dailySheet.getRange(`C2:F${daily.length}`).format.numberFormat = "#,##0";
dailySheet.getRange(`B2:B${daily.length}`).format.numberFormat = "#,##0";
setWidths(dailySheet, [120, 120, 120, 120, 120, 120, 170, 170]);

detailSheet.getRangeByIndexes(0, 0, details.length, details[0].length).values = details;
styleHeader(detailSheet.getRangeByIndexes(0, 0, 1, details[0].length));
detailSheet.tables.add(`A1:S${details.length}`, true, "LinePayDetails");
detailSheet.freezePanes.freezeRows(1);
detailSheet.freezePanes.freezeColumns(4);
detailSheet.getRange(`H2:H${details.length}`).format.numberFormat = "#,##0";
detailSheet.getRange(`N2:Q${details.length}`).format.numberFormat = "#,##0";
setWidths(detailSheet, [110, 160, 160, 150, 90, 260, 90, 120, 260, 160, 140, 120, 90, 110, 110, 110, 110, 260, 260]);

notes.showGridLines = false;
notes.getRange("A1:D1").values = [["欄位", "所在表", "說明", "備註"]];
styleHeader(notes.getRange("A1:D1"));
notes.getRange("A2:D23").values = [
  ["business_date", "LinePay明細/日彙總", "營業日期，用於 5/1~5/31 篩選。", ""],
  ["order_time", "LinePay明細", "訂單成立時間。", ""],
  ["payment_timestamp", "LinePay明細", "付款資料在 kiosk DB 的時間戳。", ""],
  ["order_id", "LinePay明細", "POS/kiosk 訂單單號。", "例如 K202605..."],
  ["display_id", "LinePay明細", "畫面顯示單號。", ""],
  ["order_guid", "LinePay明細", "訂單 GUID。", "可作內部唯一鍵"],
  ["payment_guid", "LinePay明細", "付款 GUID。", "可作付款列唯一鍵"],
  ["payment_type", "LinePay明細", "付款類型名稱。", "本檔篩選名稱包含 Line"],
  ["gross_amount", "LinePay明細/日彙總", "付款原始金額。", ""],
  ["change_amount", "LinePay明細/日彙總", "找零/差額。", "LinePay 本期間為 0"],
  ["net_amount", "LinePay明細/日彙總", "淨收金額，公式為 gross_amount - change_amount。", "建議以此對帳"],
  ["kiosk_guid", "LinePay明細", "kiosk 裝置 GUID。", ""],
  ["store_guid", "LinePay明細", "店舖 GUID。", ""],
  ["transaction_count", "日彙總", "每日 LinePay 筆數。", ""],
  ["first_order_time", "日彙總", "每日第一筆 LinePay 訂單時間。", ""],
  ["last_order_time", "日彙總", "每日最後一筆 LinePay 訂單時間。", ""],
  ["LINE transaction id", "未提供", "本機 sales_cache.sqlite 沒有此欄位。", "需 LINE 或原 POS gateway log 提供"],
  ["authorization/response code", "未提供", "本機 sales_cache.sqlite 沒有此欄位。", "需 LINE 或原 POS gateway log 提供"],
  ["settlement id", "未提供", "本機 sales_cache.sqlite 沒有此欄位。", "需 LINE 或原 POS gateway log 提供"],
  ["connection/network log", "未提供", "本機 sales_cache.sqlite 沒有此欄位。", "需 gateway 或 kiosk app log 提供"],
  ["source_db", "摘要", "匯出來源 SQLite。", meta.source_db],
  ["export_note", "摘要", meta.available_connection_fields_note, ""],
];
notes.tables.add("A1:D23", true, "FieldNotes");
notes.freezePanes.freezeRows(1);
notes.getRange("C2:D23").format = { wrapText: true };
setWidths(notes, [180, 150, 420, 360]);

const inspect = await workbook.inspect({
  kind: "table",
  range: "摘要!A1:E16",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 8,
});
console.log(inspect.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

for (const sheetName of ["摘要", "日彙總", "LinePay明細", "資料欄位說明"]) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(baseDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(JSON.stringify({ outputPath, detailRows: details.length - 1, dailyRows: daily.length - 1 }));

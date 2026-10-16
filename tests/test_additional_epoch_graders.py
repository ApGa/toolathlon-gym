"""Source-fixture reports pass; invented rows and corrupted arithmetic fail."""
import contextlib
import gzip
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import unittest

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime_patches'))


def grader(task):
    path = ROOT / 'tasks/finalpool' / task / 'evaluation/main.py'
    spec = importlib.util.spec_from_file_location('additional_epoch_grader', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed(table):
    with gzip.open(ROOT / 'db/init.sql.gz', 'rt') as stream:
        dump = stream.read()
    match = re.search(r'COPY ' + re.escape(table) + r' \(([^\n]+)\) FROM stdin;\n(.*?)\n\\\.', dump, re.S)
    columns = match[1].split(', ')
    escaped = {'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v', '\\': '\\'}
    def decode(value):
        return None if value == r'\N' else re.sub(r'\\([bfnrtv\\])', lambda m: escaped[m[1]], value)
    return [dict(zip(columns, map(decode, line.split('\t')))) for line in match[2].splitlines()]


def workbook(tables):
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in tables.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    return book


def failures(module, function, *args):
    module.PASS_COUNT = module.FAIL_COUNT = 0
    with contextlib.redirect_stdout(io.StringIO()):
        function(*args)
    return module.FAIL_COUNT


class AdditionalEpochGraders(unittest.TestCase):
    def research(self):
        module = grader('arxiv-research-pipeline-notion-excel')
        papers = [(r['id'], r['title'], json.loads(r['authors']), int(r['pub_year']), int(r['citation_count']), None)
                  for r in seed('scholarly.scholar_papers')]
        papers += [(r['id'], r['title'], json.loads(r['authors']), int(r['published'][:4]), 0, r['primary_category'])
                   for r in seed('arxiv.papers')]
        selected = sorted(papers, key=lambda r: r[4], reverse=True)[:5]
        book = workbook({
            'Paper_Catalog': [['Paper_ID', 'Title', 'Authors', 'Year', 'Category', 'Citation_Count']] + [
                [pid, title, '; '.join(module.author_names(authors)), year, category or 'Research', citations]
                for pid, title, authors, year, citations, category in selected],
            'Method_Comparison': [['Method_Name', 'Paper_Source', 'Key_Innovation', 'Benchmark_Result', 'Applicability'],
                                  ['Attention', selected[0][1], 'Source-grounded synthesis', 'Not reported', 'High']],
            'Research_Gaps': [['Gap_Area', 'Current_State', 'Opportunity', 'Priority']] + [
                [f'Gap {i}', 'Limited evidence', 'Additional experiments', 'Important'] for i in range(4)],
        })
        return module, book, papers

    def test_real_paper_metadata_and_alternative_qualitative_findings(self):
        module, book, papers = self.research()
        self.assertEqual(failures(module, module.check_research, book, papers), 0)
        book['Method_Comparison']['C2'] = 'Different supported synthesis'
        book['Research_Gaps']['A2'] = 'Another research direction'
        self.assertEqual(failures(module, module.check_research, book, papers), 0)

    def test_invented_papers_authors_and_citations_fail(self):
        module, book, papers = self.research()
        book['Paper_Catalog']['B2'] = 'Invented placeholder paper'
        book['Paper_Catalog']['C3'] = 'Author A'
        book['Paper_Catalog']['F4'] = 999999
        self.assertGreaterEqual(failures(module, module.check_research, book, papers), 3)

    def test_scholar_local_ids_and_arxiv_multiple_source_categories(self):
        module, book, papers = self.research()
        book['Paper_Catalog']['A2'] = 'scholar-selected-paper-1'
        pid, title, authors, year, citations, category = next(row for row in papers if row[5])
        for cell, value in zip(book['Paper_Catalog'][6],
                               [pid, title, '; '.join(module.author_names(authors)), year,
                                f'{category}, cs.AI', citations]):
            cell.value = value
        self.assertEqual(failures(module, module.check_research, book, papers), 0)

    def sector(self):
        module = grader('yf-sector-scholarly-excel-word')
        stocks = [(r['symbol'], json.loads(r['data'])) for r in seed('yf.stock_info')
                  if json.loads(r['data']).get('sector')]
        expected = {}
        for symbol, data in stocks:
            values = expected.setdefault(data['sector'], [0, 0, 0])
            values[0] += 1
            values[1] += data.get('currentPrice', data.get('regularMarketPrice'))
            values[2] += data['marketCap']
        titles = [r['title'] for r in seed('scholarly.scholar_papers')]
        book = workbook({
            'Sector_Performance': [['Sector', 'Stock_Count', 'Avg_Price', 'Total_Market_Value', 'Volatility_Score']] + [
                [name, count, round(prices / count, 2), cap, 1.23] for name, (count, prices, cap) in sorted(expected.items())],
            'Research_Mapping': [['Paper_Title', 'Key_Finding', 'Applicable_Sector', 'Validation_Status'],
                                 [titles[0], 'Requires additional validation', next(iter(expected)), 'Inconclusive']],
            'Investment_Thesis': [['Sector', 'Outlook', 'Supporting_Evidence', 'Risk_Factor']] + [
                [name, 'Neutral', 'Measured source data', 'Uncertain extrapolation'] for name in expected],
        })
        return module, book, {r[0] for r in stocks}, stocks, titles

    def test_real_financial_fixture_and_declared_selection(self):
        module, book, selected, stocks, papers = self.sector()
        self.assertEqual(failures(module, module.check_sectors, book, selected, stocks, papers), 0)
        self.assertEqual(module.selected_symbols([dict(data, symbol=symbol) for symbol, data in stocks]), selected)
        self.assertEqual(module.selected_symbols(dict(stocks)), selected)

    def test_sector_invented_membership_wrong_numbers_and_papers_fail(self):
        module, book, selected, stocks, papers = self.sector()
        selected.add('MADEUP')
        book['Sector_Performance']['D2'] = 170
        book['Research_Mapping']['A2'] = 'Unretrieved Market Cycles Paper'
        self.assertGreaterEqual(failures(module, module.check_sectors, book, selected, stocks, papers), 3)

    def supply(self):
        module = grader('wc-fetch-supply-chain-excel-word-gcal')
        products = [(r['name'], int(r['stock_quantity']), float(r['price'])) for r in seed('wc.products') if r['status'] == 'publish']
        suppliers = module.source_suppliers()
        chosen = suppliers[0]
        inventory, plan = [], []
        for name, quantity, price in products:
            # A declared one-unit/day estimate. The task specifies no unique demand window.
            rate = 1.0
            point = round(rate * chosen['lead_time_days'] * 1.5)
            status = 'Critical' if quantity < point else 'Warning' if quantity <= 1.2 * point else 'Healthy'
            inventory.append([name, quantity, rate, quantity, point, status])
            if status == 'Critical':
                plan.append([name, quantity, max(chosen['min_order_qty'], point - quantity), chosen['name'], '2026-03-21', 'Immediate'])
        inventory.sort(key=lambda r: r[3])
        price_by_name = {name: price for name, _, price in products}
        total_value = sum(row[2] * price_by_name[row[0]] for row in plan)
        summary = [['Total_Products', len(products)], ['Critical_Products', sum(r[5] == 'Critical' for r in inventory)],
                   ['Warning_Products', sum(r[5] == 'Warning' for r in inventory)], ['Healthy_Products', sum(r[5] == 'Healthy' for r in inventory)],
                   ['Total_Reorder_Value', round(total_value, 2)], ['Avg_Days_Until_Stockout', round(sum(r[3] for r in inventory) / len(inventory), 1)]]
        book = workbook({
            'Inventory_Status': [['Product_Name', 'Current_Stock', 'Daily_Sales_Rate', 'Days_Until_Stockout', 'Reorder_Point', 'Status']] + inventory,
            'Supplier_Analysis': [['Supplier_Name', 'Lead_Time_Days', 'Reliability_Score', 'Min_Order_Qty', 'Products_Supplied']] + [
                [s['name'], s['lead_time_days'], s['reliability_score'], s['min_order_qty'], len(products) if s == chosen else 0]
                for s in sorted(suppliers, key=lambda s: s['reliability_score'], reverse=True)],
            'Reorder_Plan': [['Product_Name', 'Current_Stock', 'Reorder_Qty', 'Supplier', 'Expected_Delivery_Date', 'Urgency']] + plan,
            'Summary': [['Metric', 'Value']] + summary,
        })
        return module, book, products, suppliers

    def test_supply_report_matches_all_real_products_and_supplier_fixture(self):
        module, book, products, suppliers = self.supply()
        self.assertEqual(len(products), 82)
        self.assertEqual(failures(module, module.check_supply_chain, book, products, suppliers), 0)

    def test_supply_invented_products_bad_stock_supplier_and_summary_fail(self):
        module, book, products, suppliers = self.supply()
        book['Inventory_Status']['A2'] = 'Blender A'
        book['Inventory_Status']['B3'] = 999999
        book['Supplier_Analysis']['C2'] = 0
        book['Summary']['B6'] = 0
        self.assertGreaterEqual(failures(module, module.check_supply_chain, book, products, suppliers), 4)

    def test_supply_reorder_formula_and_duration_are_enforced(self):
        module, book, products, suppliers = self.supply()
        book['Inventory_Status']['E2'] = 100000
        book['Inventory_Status']['D3'] = 100000
        self.assertGreaterEqual(failures(module, module.check_supply_chain, book, products, suppliers), 2)

    def test_reorder_point_uses_selected_suppliers_lead_time(self):
        module, book, products, suppliers = self.supply()
        different = next(s for s in suppliers if s['lead_time_days'] != suppliers[0]['lead_time_days'])
        book['Reorder_Plan']['D2'] = different['name']
        self.assertGreater(failures(module, module.check_supply_chain, book, products, suppliers), 0)

    def test_missing_sector_metadata_fails_without_grader_crash(self):
        module, book, selected, stocks, papers = self.sector()
        selected.add('GC=F')
        stocks.append(('GC=F', {'currentPrice': 5093.3}))
        self.assertGreater(failures(module, module.check_sectors, book, selected, stocks, papers), 0)

    def test_preprocessors_keep_the_source_research_corpus(self):
        for name in ['arxiv-research-pipeline-notion-excel', 'yf-sector-scholarly-excel-word']:
            source = (ROOT / 'tasks/finalpool' / name / 'preprocess/main.py').read_text()
            self.assertNotIn('DELETE FROM scholarly.', source)
            self.assertNotIn('DELETE FROM arxiv.', source)
            self.assertNotIn('DELETE FROM arxiv_latex.', source)


if __name__ == '__main__':
    unittest.main()

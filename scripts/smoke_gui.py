#!/usr/bin/env python3
"""Exercise the real GUI against an isolated local workspace with fictional inputs."""

import argparse
import csv
import io
from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Running loopback GUI with an isolated test data directory')
    parser.add_argument('--output', type=Path, default=Path('dev/qa'))
    args = parser.parse_args()
    if not args.url.startswith('http://127.0.0.1:'):
        parser.error('Use an isolated loopback application.')
    args.output.mkdir(parents=True, exist_ok=True)
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.on('response', lambda response: failures.append(f'{response.status} {response.url}') if response.status >= 400 else None)
        page.goto(args.url)
        page.wait_for_load_state('networkidle')
        sample = page.get_by_role('button', name='Load sample', exact=True)
        if sample.count():
            page.screenshot(path=str(args.output / 'welcome.png'), full_page=True)
            sample.click()
        else:
            picker=page.get_by_label('Search', exact=True)
            demo_option=picker.locator('option').filter(has_text='Systems & operations').first
            picker.select_option(demo_option.get_attribute('value'))
        expect(page.locator('tr[data-opportunity]')).to_have_count(5, timeout=30000)
        expect(page.locator('[data-action=run]').first).to_be_enabled(timeout=30000)
        page.locator('[data-action=run]').first.click()
        expect(page.locator('[data-action=run]').first).to_be_enabled(timeout=30000)
        expect(page.locator('#job-region')).to_be_empty(timeout=30000)
        assert not page.locator('.sidebar,.report-context,.metrics-strip,#run-stage').count()
        assert page.locator('tr[data-opportunity]').first.bounding_box()['y'] < 300
        page.screenshot(path=str(args.output / 'opportunities.png'), full_page=True)
        page.get_by_label('Opportunity type', exact=True).select_option('company_lead')
        expect(page.locator('tr[data-opportunity]')).to_have_count(1)
        page.get_by_label('Opportunity type', exact=True).select_option('all')
        page.get_by_role('searchbox', name='Search roles or companies').fill('Northline')
        expect(page.locator('tr[data-opportunity]')).to_have_count(1)
        page.get_by_role('searchbox', name='Search roles or companies').fill('')
        page.locator('[data-action=open-opportunity]').first.click()
        expect(page.get_by_role('dialog', name='Operations Engineer')).to_be_visible()
        page.get_by_label('Your notes', exact=True).fill('QA: review the role requirements.')
        page.get_by_label('Follow-up date', exact=True).fill('2027-01-15')
        page.locator('#lead-dialog [data-action=shortlist-opportunity]').click()
        expect(page.get_by_role('button', name='Shortlisted ✓')).to_be_visible()
        page.screenshot(path=str(args.output / 'opportunity-review.png'), full_page=True)
        page.get_by_role('tab', name='Evidence', exact=True).click()
        page.screenshot(path=str(args.output / 'evidence.png'), full_page=True)
        page.get_by_role('tab', name='Evidence', exact=True).press('ArrowLeft')
        expect(page.get_by_role('tab', name='Outreach', exact=False)).to_have_attribute('aria-selected', 'true')
        page.screenshot(path=str(args.output / 'outreach.png'), full_page=True)
        page.get_by_role('button', name='Close opportunity').click()
        page.get_by_role('button', name='Save to shortlist', exact=True).first.click()
        expect(page.get_by_role('button', name='Remove from shortlist', exact=True)).to_have_count(2)
        page.get_by_role('button', name='Remove from shortlist', exact=True).nth(1).click()
        page.get_by_role('button', name='Shortlist', exact=False).first.click()
        expect(page.locator('tr[data-opportunity]')).to_have_count(1)
        page.get_by_role('button', name='Jobs', exact=False).first.click()
        expect(page.locator('tr[data-opportunity]')).to_have_count(5)
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='CSV', exact=True).click()
        rows = list(csv.DictReader(io.StringIO(Path(downloaded.value.path()).read_text('utf-8-sig'))))
        assert len(rows) == 5 and rows[0]['apply_url'] and rows[0]['review_notes'].startswith('QA:')
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Excel', exact=True).click()
        book = load_workbook(io.BytesIO(Path(downloaded.value.path()).read_bytes()))
        assert book.sheetnames == ['Report', 'Opportunities', 'Sources', 'Read me']
        assert book['Report']['H12'].hyperlink
        page.get_by_role('button', name='Export options', exact=True).click()
        page.screenshot(path=str(args.output / 'downloads.png'), full_page=True)
        page.locator('input[name=export-scope][value=shortlist]').check()
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Download CSV', exact=True).click()
        assert len(list(csv.DictReader(io.StringIO(Path(downloaded.value.path()).read_text('utf-8-sig'))))) == 1
        page.locator('input[name=export-scope][value=apply]').check()
        expect(page.get_by_role('button', name='Download CSV', exact=True)).to_be_disabled()
        page.get_by_role('button', name='Search settings', exact=True).click()
        page.screenshot(path=str(args.output / 'search.png'), full_page=True)
        page.locator('.route-grid').screenshot(path=str(args.output / 'agent-routes.png'))
        page.get_by_role('button', name='Jobs', exact=False).click()
        for width in (320, 390, 768):
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), width
            if width == 390:
                page.screenshot(path=str(args.output / 'mobile.png'), full_page=True)
                page.locator('[data-action=open-opportunity]').first.click()
                page.get_by_role('tab', name='Outreach', exact=False).click()
                assert page.evaluate('document.querySelector("#lead-dialog").scrollWidth <= document.querySelector("#lead-dialog").clientWidth')
                page.screenshot(path=str(args.output / 'mobile-outreach.png'), full_page=True)
                page.get_by_role('button', name='Close opportunity').click()
        page.set_viewport_size({'width': 1440, 'height': 1100})
        page.get_by_role('button', name='New search', exact=False).click()
        dialog = page.locator('#campaign-dialog')
        dialog.get_by_label('Name', exact=True).fill('QA – imported job report')
        dialog.get_by_label('What are you looking for?', exact=True).fill('Find fictional engineering roles for a browser test.')
        dialog.get_by_label('Locations', exact=True).fill('Los Angeles, CA; San Diego, CA')
        dialog.get_by_role('button', name='Create search', exact=False).click()
        expect(page.locator('#search-form')).to_be_visible()
        page.get_by_label('Minimum annual salary', exact=True).fill('120000')
        page.get_by_label('Target roles', exact=True).fill('Engineer')
        page.get_by_role('button', name='Save search', exact=False).click()
        page.locator('#documents-input').set_input_files({'name':'resume.txt', 'mimeType':'text/plain', 'buffer':b'Fictional candidate. Designed test fixtures.'})
        expect(page.locator('.document-item')).to_have_count(1)
        page.get_by_role('button', name='Remove resume.txt').click()
        expect(page.locator('.document-item')).to_have_count(0)
        page.get_by_role('button', name='Jobs', exact=False).click()
        expect(page.get_by_role('button', name='CSV', exact=True)).to_be_disabled()
        page.get_by_role('button', name='Import', exact=True).click()
        page.locator('#import-file').set_input_files({'name':'jobs.csv', 'mimeType':'text/csv', 'buffer':b'company,title,listing_url,apply_url,location\nQA Example,Fictional Engineer,https://jobs.example.invalid/role-1,https://jobs.example.invalid/apply-1,"Los Angeles, CA"\n'})
        page.locator('#import-form').get_by_role('button', name='Import', exact=True).click()
        expect(page.locator('tr[data-opportunity]')).to_have_count(1)
        job_link = page.get_by_role('link', name='Open job', exact=False)
        expect(job_link).to_have_attribute('href', 'https://jobs.example.invalid/apply-1')
        page.context.route('https://jobs.example.invalid/**', lambda route: route.fulfill(body='Fictional application page for the GUI test.'))
        with page.expect_popup() as popup:
            job_link.click()
        expect(popup.value.locator('body')).to_contain_text('Fictional application page')
        popup.value.close()
        page.reload()
        expect(page.locator('tr[data-opportunity]')).to_have_count(1)
        page.get_by_role('button', name='Search settings', exact=True).click()
        expect(page.get_by_label('Minimum annual salary', exact=True)).to_have_value('120000')
        expect(page.get_by_label('Locations', exact=True)).to_have_value('Los Angeles, CA; San Diego, CA')
        assert not failures, failures
        browser.close()
    print('PASS: report run; filters; review; shortlist; CSV/XLSX; export guards; mobile; search save; documents; import; job link; reload. Zero JS/HTTP errors.')


if __name__ == '__main__':
    main()

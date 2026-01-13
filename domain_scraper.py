#!/usr/bin/env python3
"""
Domain Scraper - Checks domains for productive, advertising-suitable websites
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import concurrent.futures
import time
import re
from typing import Dict, List, Tuple
import logging
import json
from datetime import datetime
import os
from dotenv import load_dotenv
import base64

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CentralNICAPI:
    """Handler for CentralNIC Registry API"""
    
    def __init__(self):
        """Initialize API client with credentials from .env file"""
        self.username = os.getenv('API_USERNAME')
        self.password = os.getenv('API_PASSWORD')
        self.base_url = os.getenv('API_BASE_URL', 'https://registry-api.centralnic.com/v2')
        self.tlds = os.getenv('API_TLDS', 'sbs,icu,cyou,cfd,bond').split(',')
        
        if not self.username or not self.password:
            raise ValueError("API_USERNAME and API_PASSWORD must be set in .env file")
        
        # Create Basic Auth header
        credentials = f"{self.username}:{self.password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            'Authorization': f'Basic {encoded_credentials}'
        }
    
    def fetch_domains_for_tld(self, tld: str) -> List[str]:
        """
        Fetch domains for a specific TLD
        
        Args:
            tld: The top-level domain (e.g., 'sbs', 'icu')
            
        Returns:
            List of domain names for that TLD
        """
        try:
            url = f"{self.base_url}/{tld}/domains"
            logger.info(f"Fetching domains for .{tld} from {url}...")
            
            response = requests.get(
                url,
                headers=self.headers,
                timeout=120
            )
            response.raise_for_status()
            
            # Parse JSON response
            data = response.json()
            
            # Extract domain names from response
            domains = []
            if isinstance(data, dict) and 'domains' in data:
                domains = [item['domain'] for item in data['domains'] if 'domain' in item]
            elif isinstance(data, list):
                domains = [item['domain'] for item in data if 'domain' in item]
            
            logger.info(f"Successfully fetched {len(domains)} domains for .{tld}")
            return domains
            
        except requests.RequestException as e:
            logger.error(f"Error fetching domains for .{tld}: {e}")
            return []
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error parsing API response for .{tld}: {e}")
            return []
    
    def fetch_domains(self) -> List[str]:
        """
        Fetch domains from CentralNIC API for all configured TLDs
        
        Returns:
            List of all domain names across all TLDs
        """
        all_domains = []
        
        logger.info(f"Fetching domains for TLDs: {', '.join(self.tlds)}")
        
        for tld in self.tlds:
            tld = tld.strip()
            domains = self.fetch_domains_for_tld(tld)
            all_domains.extend(domains)
        
        logger.info(f"Successfully fetched {len(all_domains)} total domains across {len(self.tlds)} TLDs")
        return all_domains


class DomainChecker:
    """Checks domains for productivity and advertising suitability"""
    
    # Keywords for non-advertising-suitable pages
    PARKING_KEYWORDS = [
        'domain parking', 'domain for sale', 'buy this domain',
        'domain kaufen', 'diese domain steht zum verkauf',
        'domain geparkt', 'coming soon', 'under construction',
        'im aufbau', 'in bearbeitung', 'sedo', 'parked domain',
        'domain zu verkaufen', 'domain is for sale'
    ]
    
    STANDARD_PAGES = [
        'apache', 'nginx', 'default page', 'test page',
        'it works', 'welcome to nginx', 'apache2 debian default',
        'placeholder', 'default web page', 'server test page'
    ]
    
    ERROR_INDICATORS = [
        '404', 'not found', 'error', 'fehler',
        'page not found', 'seite nicht gefunden'
    ]
    
    def __init__(self, timeout: int = 10, max_workers: int = 10):
        """
        Initializes the Domain Checker
        
        Args:
            timeout: Timeout for HTTP requests in seconds
            max_workers: Maximum number of parallel workers
        """
        self.timeout = timeout
        self.max_workers = max_workers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def check_domain(self, domain: str) -> Dict:
        """
        Checks a single domain
        
        Args:
            domain: The domain to check
            
        Returns:
            Dictionary with check results
        """
        result = {
            'domain': domain,
            'status': 'unknown',
            'is_advertising_suitable': False,
            'http_status': None,
            'title': None,
            'content_length': 0,
            'has_content': False,
            'reason': None,
            'checked_at': datetime.now().isoformat()
        }
        
        try:
            # Try both protocols
            for protocol in ['https', 'http']:
                url = f"{protocol}://{domain.strip()}"
                try:
                    response = self.session.get(
                        url,
                        timeout=self.timeout,
                        allow_redirects=True,
                        verify=False  # Ignore SSL errors
                    )
                    
                    result['http_status'] = response.status_code
                    
                    if response.status_code == 200:
                        # Analyze content
                        html_content = response.text
                        result['content_length'] = len(html_content)
                        
                        soup = BeautifulSoup(html_content, 'html.parser')
                        
                        # Extract title
                        title_tag = soup.find('title')
                        if title_tag:
                            result['title'] = title_tag.get_text().strip()
                        
                        # Content analysis
                        text_content = soup.get_text().lower()
                        
                        # Perform checks
                        if self._is_parking_page(text_content, result['title']):
                            result['status'] = 'parking'
                            result['reason'] = 'Domain parking detected'
                        elif self._is_standard_page(text_content, result['title']):
                            result['status'] = 'standard_page'
                            result['reason'] = 'Standard/test page detected'
                        elif self._is_error_page(text_content, result['title']):
                            result['status'] = 'error'
                            result['reason'] = 'Error page detected'
                        elif self._has_minimal_content(soup, html_content):
                            result['status'] = 'productive'
                            result['is_advertising_suitable'] = True
                            result['has_content'] = True
                            result['reason'] = 'Productive website with content'
                        else:
                            result['status'] = 'insufficient_content'
                            result['reason'] = 'Insufficient content'
                        
                        break  # Success, no further attempts needed
                        
                except requests.RequestException:
                    continue  # Try next protocol
            
            if result['http_status'] is None:
                result['status'] = 'unreachable'
                result['reason'] = 'Domain unreachable'
                
        except Exception as e:
            result['status'] = 'error'
            result['reason'] = f'Error: {str(e)}'
            logger.error(f"Error checking {domain}: {e}")
        
        return result
    
    def _is_parking_page(self, text_content: str, title: str) -> bool:
        """Checks for parking pages"""
        check_text = text_content + (title.lower() if title else '')
        return any(keyword in check_text for keyword in self.PARKING_KEYWORDS)
    
    def _is_standard_page(self, text_content: str, title: str) -> bool:
        """Checks for standard/test pages"""
        check_text = text_content + (title.lower() if title else '')
        return any(keyword in check_text for keyword in self.STANDARD_PAGES)
    
    def _is_error_page(self, text_content: str, title: str) -> bool:
        """Checks for error pages"""
        check_text = text_content + (title.lower() if title else '')
        # Only count as error if it appears prominently
        return ('404' in check_text and 'not found' in check_text)
    
    def _has_minimal_content(self, soup: BeautifulSoup, html_content: str) -> bool:
        """
        Checks if the page has minimal content
        
        Criteria:
        - At least 200 characters of content
        - Has multiple HTML elements (not just skeleton)
        - Has text content
        """
        # Extract text content
        text_content = soup.get_text(strip=True)
        
        if len(text_content) < 200:
            return False
        
        # Check for structured content
        paragraphs = soup.find_all(['p', 'article', 'section', 'div'])
        if len(paragraphs) < 5:
            return False
        
        # Check for navigation/links (sign of a real website)
        links = soup.find_all('a')
        if len(links) < 3:
            return False
        
        return True
    
    def check_domains_from_file(self, filepath: str) -> List[Dict]:
        """
        Checks domains from a file
        
        Args:
            filepath: Path to file with domains (one per line)
            
        Returns:
            List of check results
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            domains = [line.strip() for line in f if line.strip()]
        
        return self.check_domains(domains)
    
    def check_domains(self, domains: List[str]) -> List[Dict]:
        """
        Checks multiple domains in parallel
        
        Args:
            domains: List of domains to check
            
        Returns:
            List of check results
        """
        results = []
        total = len(domains)
        
        logger.info(f"Starting check of {total} domains...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_domain = {
                executor.submit(self.check_domain, domain): domain 
                for domain in domains
            }
            
            completed = 0
            for future in concurrent.futures.as_completed(future_to_domain):
                completed += 1
                try:
                    result = future.result()
                    results.append(result)
                    
                    # Show progress
                    if completed % 10 == 0 or completed == total:
                        logger.info(f"Progress: {completed}/{total} domains checked")
                        
                except Exception as e:
                    domain = future_to_domain[future]
                    logger.error(f"Error checking {domain}: {e}")
        
        return results
    
    def save_results(self, results: List[Dict], output_file: str = 'results.json'):
        """Saves results to JSON file"""
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to {output_file}")
    
    def generate_report(self, results: List[Dict]) -> str:
        """Generates a summary report"""
        total = len(results)
        advertising_suitable = sum(1 for r in results if r['is_advertising_suitable'])
        
        status_counts = {}
        for result in results:
            status = result['status']
            status_counts[status] = status_counts.get(status, 0) + 1
        
        report = f"""
╔══════════════════════════════════════════════════════════════╗
║          DOMAIN SCRAPER - EVALUATION REPORT                  ║
╚══════════════════════════════════════════════════════════════╝

Total domains checked: {total}
Advertising-suitable domains: {advertising_suitable} ({advertising_suitable/total*100:.1f}%)

Status distribution:
"""
        for status, count in sorted(status_counts.items(), key=lambda x: x[1], reverse=True):
            percentage = count / total * 100
            report += f"  • {status}: {count} ({percentage:.1f}%)\n"
        
        report += "\n" + "="*65 + "\n"
        report += "ADVERTISING-SUITABLE DOMAINS:\n"
        report += "="*65 + "\n"
        
        suitable_domains = [r for r in results if r['is_advertising_suitable']]
        for result in suitable_domains[:20]:  # Show top 20
            report += f"\n✓ {result['domain']}\n"
            if result['title']:
                report += f"  Title: {result['title'][:60]}...\n" if len(result['title']) > 60 else f"  Title: {result['title']}\n"
            report += f"  Status: {result['http_status']} | Content: {result['content_length']} characters\n"
        
        if len(suitable_domains) > 20:
            report += f"\n... and {len(suitable_domains) - 20} more\n"
        
        return report


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Checks domains for productive, advertising-suitable websites'
    )
    parser.add_argument(
        'input_file',
        nargs='?',
        help='File with domains (one per line). If omitted, domains will be fetched from CentralNIC API'
    )
    parser.add_argument(
        '--api',
        action='store_true',
        help='Fetch domains from CentralNIC API instead of file'
    )
    parser.add_argument(
        '-o', '--output',
        default='results.json',
        help='Output file for results (default: results.json)'
    )
    parser.add_argument(
        '-w', '--workers',
        type=int,
        default=10,
        help='Number of parallel workers (default: 10)'
    )
    parser.add_argument(
        '-t', '--timeout',
        type=int,
        default=10,
        help='Timeout per request in seconds (default: 10)'
    )
    parser.add_argument(
        '--report',
        action='store_true',
        help='Display a detailed report'
    )
    
    args = parser.parse_args()
    
    # Suppress warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # Initialize Domain Checker
    checker = DomainChecker(timeout=args.timeout, max_workers=args.workers)
    
    # Get domains either from API or file
    if args.api or not args.input_file:
        # Fetch from API
        try:
            api = CentralNICAPI()
            domains = api.fetch_domains()
            results = checker.check_domains(domains)
        except Exception as e:
            logger.error(f"Failed to fetch domains from API: {e}")
            print(f"Error: Could not fetch domains from API. {e}")
            return 1
    else:
        # Load from file
        results = checker.check_domains_from_file(args.input_file)
    
    # Save results
    checker.save_results(results, args.output)
    
    # Display report
    if args.report:
        report = checker.generate_report(results)
        print(report)
    else:
        # Short summary
        total = len(results)
        suitable = sum(1 for r in results if r['is_advertising_suitable'])
        print(f"\n✓ Done! {suitable}/{total} advertising-suitable domains found.")
        print(f"Details in {args.output}")


if __name__ == '__main__':
    main()

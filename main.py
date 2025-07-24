import csv
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import argparse
import sys
from urllib.parse import urlparse
import re
import json
import random
from collections import defaultdict

class MealPrepCalendarGenerator:
    def __init__(self, seed=None):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        if seed is not None:
            random.seed(seed)
        
        # Track recipe usage to ensure variety
        self.recipe_usage = defaultdict(list)  # url -> list of week numbers used
    
    def extract_title(self, url):
        """Extract title from a webpage, with special handling for recipe sites"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            title = None
            
            # Method 1: Look for recipe-specific meta tags
            recipe_name = soup.find('meta', property='og:title')
            if recipe_name:
                title = recipe_name.get('content')
            
            # Method 2: Look for JSON-LD structured data
            if not title:
                scripts = soup.find_all('script', type='application/ld+json')
                for script in scripts:
                    try:
                        data = json.loads(script.string)
                        if isinstance(data, list):
                            data = data[0]
                        if data.get('@type') == 'Recipe' or 'Recipe' in str(data.get('@type', '')):
                            title = data.get('name')
                            break
                    except:
                        continue
            
            # Method 3: Look for h1 tags
            if not title:
                h1_tags = soup.find_all('h1')
                for h1 in h1_tags:
                    if h1.get_text().strip():
                        title = h1.get_text().strip()
                        break
            
            # Method 4: Fall back to page title
            if not title:
                title_tag = soup.find('title')
                if title_tag:
                    title = title_tag.get_text().strip()
            
            # Clean up the title
            if title:
                title = re.sub(r'\s*[-|]\s*(Recipe|Recipes|Cooking|Kitchen|Food).*$', '', title, flags=re.IGNORECASE)
                title = title.strip()
                
                if len(title) > 60:
                    title = title[:57] + "..."
            
            return title or "Recipe from " + urlparse(url).netloc
            
        except requests.RequestException as e:
            print(f"Error fetching {url}: {e}")
            return f"Recipe from {urlparse(url).netloc}"
        except Exception as e:
            print(f"Error parsing {url}: {e}")
            return f"Recipe from {urlparse(url).netloc}"
    
    def select_recipes_for_week(self, urls, week_num, previous_week_urls=None):
        """
        Select lunch and dinner recipes for a week with smart filtering
        
        Args:
            urls (list): Available recipe URLs
            week_num (int): Current week number (1-based)
            previous_week_urls (list): URLs used in previous week [lunch_url, dinner_url]
        
        Returns:
            tuple: (lunch_url, dinner_url)
        """
        # Start with all available URLs
        available_urls = urls.copy()
        
        # Remove previous week's recipes
        if previous_week_urls:
            for prev_url in previous_week_urls:
                if prev_url in available_urls:
                    available_urls.remove(prev_url)
                    print(f"    Excluding {prev_url.split('/')[-1]} (used last week)")
        
        # If we have very few options left, be more lenient
        if len(available_urls) < 2:
            print("    Warning: Limited unique recipes available, allowing some recent repeats")
            available_urls = urls.copy()
            
            # Only exclude if used in the immediate previous week
            if previous_week_urls and week_num > 1:
                for prev_url in previous_week_urls:
                    if prev_url in available_urls and len(available_urls) > 2:
                        available_urls.remove(prev_url)
        
        # Select lunch recipe
        lunch_url = random.choice(available_urls)
        
        # Select dinner recipe (different from lunch)
        dinner_options = [url for url in available_urls if url != lunch_url]
        if not dinner_options:
            # If no other options, use any recipe except lunch
            dinner_options = [url for url in urls if url != lunch_url]
        
        dinner_url = random.choice(dinner_options) if dinner_options else lunch_url
        
        # Track usage
        self.recipe_usage[lunch_url].append(week_num)
        self.recipe_usage[dinner_url].append(week_num)
        
        return lunch_url, dinner_url
    
    def create_week_events(self, lunch_url, lunch_title, dinner_url, dinner_title, start_date, week_num):
        """Create events for one week"""
        events = []
        
        # Create prep day event with BOTH recipes
        prep_event = {
            'Subject': f"Prep: {lunch_title} & {dinner_title}",
            'Start Date': start_date.strftime('%m/%d/%Y'),
            'Start Time': '',
            'End Date': start_date.strftime('%m/%d/%Y'),
            'End Time': '',
            'All Day Event': 'True',
            'Description': f"Meal prep for Week {week_num}:\n\nLunch Recipe: {lunch_title}\n{lunch_url}\n\nDinner Recipe: {dinner_title}\n{dinner_url}",
            'Location': 'Kitchen',
            'Private': 'False'
        }
        events.append(prep_event)
        
        # Create different lunch and dinner events for 5 days
        for i in range(1, 6):  # Days 1-5 after prep day
            meal_date = start_date + timedelta(days=i)
            
            # Lunch event
            lunch_event = {
                'Subject': f"Lunch: {lunch_title}",
                'Start Date': meal_date.strftime('%m/%d/%Y'),
                'Start Time': '',
                'End Date': meal_date.strftime('%m/%d/%Y'),
                'End Time': '',
                'All Day Event': 'True',
                'Description': f"Lunch - {lunch_title}\nRecipe: {lunch_url}",
                'Location': '',
                'Private': 'False'
            }
            events.append(lunch_event)
            
            # Dinner event
            dinner_event = {
                'Subject': f"Dinner: {dinner_title}",
                'Start Date': meal_date.strftime('%m/%d/%Y'),
                'Start Time': '',
                'End Date': meal_date.strftime('%m/%d/%Y'),
                'End Time': '',
                'All Day Event': 'True',
                'Description': f"Dinner - {dinner_title}\nRecipe: {dinner_url}",
                'Location': '',
                'Private': 'False'
            }
            events.append(dinner_event)
        
        return events
    
    def create_meal_prep_calendar(self, urls, output_file="meal_prep_calendar.csv", 
                                 start_date=None, num_weeks=4):
        """Create a comprehensive meal prep calendar with smart recipe selection"""
        if start_date is None:
            start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        if len(urls) < 2:
            print("Error: Need at least 2 recipes")
            return
        
        all_events = []
        week_summary = []
        previous_week_urls = None
        
        print(f"Creating meal prep calendar for {num_weeks} weeks...")
        print(f"Available recipes: {len(urls)}")
        
        # Generate weeks
        for week_num in range(1, num_weeks + 1):
            print(f"\nWeek {week_num}:")
            
            # Select recipes for this week
            lunch_url, dinner_url = self.select_recipes_for_week(
                urls, week_num, previous_week_urls
            )
            
            # Extract titles
            print(f"  Processing lunch recipe: {lunch_url}")
            lunch_title = self.extract_title(lunch_url)
            print(f"  Processing dinner recipe: {dinner_url}")
            dinner_title = self.extract_title(dinner_url)
            
            print(f"  → Lunch: {lunch_title}")
            print(f"  → Dinner: {dinner_title}")
            
            # Calculate start date for this week
            week_start_date = start_date + timedelta(days=(week_num - 1) * 7)
            
            # Create events for this week
            week_events = self.create_week_events(
                lunch_url=lunch_url,
                lunch_title=lunch_title,
                dinner_url=dinner_url,
                dinner_title=dinner_title,
                start_date=week_start_date,
                week_num=week_num
            )
            
            all_events.extend(week_events)
            week_summary.append({
                'week_num': week_num,
                'lunch_title': lunch_title,
                'dinner_title': dinner_title,
                'prep_date': week_start_date,
                'lunch_url': lunch_url,
                'dinner_url': dinner_url
            })
            
            # Update previous week URLs for next iteration
            previous_week_urls = [lunch_url, dinner_url]
        
        # Write to CSV
        fieldnames = ['Subject', 'Start Date', 'Start Time', 'End Date', 'End Time', 
                     'All Day Event', 'Description', 'Location', 'Private']
        
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_events)
        
        # Print summary
        print(f"\nCSV file created: {output_file}")
        print(f"Created {len(all_events)} calendar events")
        print(f"Generated {num_weeks} weeks of meal prep")
        
        print("\nMeal Plan Summary:")
        print("=" * 80)
        for week in week_summary:
            prep_date = week['prep_date']
            meal_start = prep_date + timedelta(days=1)
            meal_end = prep_date + timedelta(days=5)
            
            print(f"Week {week['week_num']}:")
            print(f"  Prep: {prep_date.strftime('%A, %B %d')}")
            print(f"  Meals: {meal_start.strftime('%B %d')} - {meal_end.strftime('%B %d')}")
            print(f"  Lunch: {week['lunch_title']}")
            print(f"  Dinner: {week['dinner_title']}")
            print()
        
        # Print recipe usage statistics
        print("Recipe Usage Summary:")
        print("-" * 40)
        for url, weeks in self.recipe_usage.items():
            recipe_name = url.split('/')[-1][:30]
            print(f"{recipe_name}: Used in weeks {', '.join(map(str, weeks))}")
        
        return output_file

def read_urls_from_file(filename):
    """Read URLs from a text file (one URL per line)"""
    try:
        with open(filename, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        return urls
    except FileNotFoundError:
        print(f"Error: File {filename} not found")
        return []

def main():
    parser = argparse.ArgumentParser(
        description="Create meal prep calendar with smart recipe repetition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Features:
- Randomly selects different lunch and dinner recipes each week
- Avoids using the same recipe as the previous week
- Allows recipe repetition across non-consecutive weeks
- Tracks recipe usage for variety

Example recipes.txt:
https://example.com/chicken-salad
https://example.com/beef-stew
https://example.com/turkey-sandwich
https://example.com/pasta-dish
https://example.com/quinoa-bowl
https://example.com/salmon-dinner
        """
    )
    
    parser.add_argument('urls', nargs='*', help='Recipe URLs (or use --file)')
    parser.add_argument('--file', '-f', help='File containing URLs (one per line)')
    parser.add_argument('--output', '-o', default='meal_prep_calendar.csv', 
                       help='Output CSV filename')
    parser.add_argument('--start-date', help='Start date for first week (YYYY-MM-DD)')
    parser.add_argument('--weeks', '-w', type=int, default=4, 
                       help='Number of weeks to generate (default: 4)')
    parser.add_argument('--seed', type=int, help='Random seed for consistent results')
    
    args = parser.parse_args()
    
    # Get URLs
    urls = []
    if args.file:
        urls.extend(read_urls_from_file(args.file))
    if args.urls:
        urls.extend(args.urls)
    
    if not urls:
        print("No URLs provided. Use --help for usage information.")
        sys.exit(1)
    
    # Parse start date
    start_date = None
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
        except ValueError:
            print("Invalid date format. Use YYYY-MM-DD")
            sys.exit(1)
    
    # Create calendar
    generator = MealPrepCalendarGenerator(seed=args.seed)
    generator.create_meal_prep_calendar(urls, args.output, start_date, args.weeks)
    
    print(f"\nTo import into Google Calendar:")
    print("1. Go to Google Calendar")
    print("2. Click the '+' next to 'Other calendars'")
    print("3. Select 'Import'")
    print(f"4. Upload the file: {args.output}")

if __name__ == "__main__":
    main()
import csv
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import argparse
import sys
from urllib.parse import urlparse, unquote
import re
import json
import random
import os
import ollama

class MealPrepCalendarGenerator:
    def __init__(self, seed=None, allergies_file=None):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        if seed is not None:
            random.seed(seed)
        
        # Load allergies from file
        self.allergies = []
        if allergies_file and os.path.exists(allergies_file):
            with open(allergies_file, 'r', encoding='utf-8') as f:
                self.allergies = [line.strip() for line in f if line.strip()]

    def extract_recipe_content(self, url):
        """Extract recipe content from URL, focusing on ingredients section"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            recipe_content = {}
            
            # Try to extract structured recipe data first (most reliable)
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        for entry in data:
                            if isinstance(entry, dict) and ('Recipe' in str(entry.get('@type', ''))):
                                recipe_content = self._extract_structured_recipe_data(entry)
                                break
                    elif isinstance(data, dict) and ('Recipe' in str(data.get('@type', ''))):
                        recipe_content = self._extract_structured_recipe_data(data)
                        break
                        
                    if recipe_content:
                        break
                except Exception:
                    continue
            
            # If no structured data, look for ingredients section specifically
            if not recipe_content.get('ingredients'):
                ingredients = self._extract_ingredients_from_html(soup)
                if ingredients:
                    recipe_content['ingredients'] = ingredients
            
            # Get recipe name if not already found
            if not recipe_content.get('name'):
                recipe_content['name'] = self._extract_recipe_name(soup)
            
            # Format for AI analysis
            formatted_content = self._format_recipe_for_analysis(recipe_content)
            return formatted_content[:4000]  # Limit length
            
        except Exception as e:
            print(f"Warning: Could not extract recipe content from {url}: {e}")
            return ""

    def _extract_structured_recipe_data(self, recipe_data):
        """Extract data from JSON-LD structured recipe data"""
        content = {}
        
        if 'name' in recipe_data:
            content['name'] = recipe_data['name']
        
        if 'recipeIngredient' in recipe_data:
            content['ingredients'] = recipe_data['recipeIngredient']
        
        if 'recipeInstructions' in recipe_data:
            instructions = []
            for instruction in recipe_data['recipeInstructions']:
                if isinstance(instruction, dict):
                    instructions.append(instruction.get('text', ''))
                else:
                    instructions.append(str(instruction))
            content['instructions'] = instructions[:3]  # Limit to first 3 steps
        
        return content

    def _extract_ingredients_from_html(self, soup):
        """Extract ingredients from HTML by looking for ingredients sections"""
        ingredients = []
        
        # Common patterns for ingredients sections
        ingredient_patterns = [
            # Look for headings that say "ingredients"
            {'tag': ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'], 
             'text_pattern': r'ingredients?', 'flags': re.IGNORECASE},
            # Look for elements with ingredient-related classes/ids
            {'class_pattern': r'ingredient', 'flags': re.IGNORECASE},
            {'id_pattern': r'ingredient', 'flags': re.IGNORECASE},
        ]
        
        for pattern in ingredient_patterns:
            if 'tag' in pattern:
                # Look for ingredient headings
                headings = soup.find_all(pattern['tag'])
                for heading in headings:
                    if re.search(pattern['text_pattern'], heading.get_text(), pattern['flags']):
                        # Found ingredients heading, look for list after it
                        ingredients = self._extract_list_after_element(heading)
                        if ingredients:
                            return ingredients
            
            if 'class_pattern' in pattern:
                # Look for elements with ingredient classes
                elements = soup.find_all(class_=re.compile(pattern['class_pattern'], pattern['flags']))
                for element in elements:
                    found_ingredients = self._extract_ingredients_from_element(element)
                    if found_ingredients:
                        ingredients.extend(found_ingredients)
            
            if 'id_pattern' in pattern:
                # Look for elements with ingredient IDs
                elements = soup.find_all(id=re.compile(pattern['id_pattern'], pattern['flags']))
                for element in elements:
                    found_ingredients = self._extract_ingredients_from_element(element)
                    if found_ingredients:
                        ingredients.extend(found_ingredients)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_ingredients = []
        for ingredient in ingredients:
            if ingredient.lower() not in seen:
                seen.add(ingredient.lower())
                unique_ingredients.append(ingredient)
        
        return unique_ingredients[:20]  # Limit to 20 ingredients max

    def _extract_list_after_element(self, element):
        """Extract list items that come after a given element"""
        ingredients = []
        
        # Look for the next sibling that's a list
        current = element.next_sibling
        while current and len(ingredients) < 20:
            if hasattr(current, 'name'):
                if current.name in ['ul', 'ol']:
                    # Found a list, extract items
                    items = current.find_all('li')
                    for item in items:
                        text = item.get_text().strip()
                        if text and len(text) < 200:  # Reasonable ingredient length
                            ingredients.append(text)
                    break
                elif current.name in ['div', 'section']:
                    # Look inside div/section for lists
                    lists = current.find_all(['ul', 'ol'])
                    for list_elem in lists:
                        items = list_elem.find_all('li')
                        for item in items:
                            text = item.get_text().strip()
                            if text and len(text) < 200:
                                ingredients.append(text)
                    if ingredients:
                        break
            current = current.next_sibling
        
        return ingredients

    def _extract_ingredients_from_element(self, element):
        """Extract ingredients from a specific element"""
        ingredients = []
        
        # Look for lists within the element
        lists = element.find_all(['ul', 'ol'])
        for list_elem in lists:
            items = list_elem.find_all('li')
            for item in items:
                text = item.get_text().strip()
                if text and len(text) < 200:  # Reasonable ingredient length
                    ingredients.append(text)
        
        # If no lists found, look for individual items with checkboxes or bullets
        if not ingredients:
            # Look for elements that might be individual ingredients
            potential_ingredients = element.find_all(['p', 'div', 'span'], 
                                                   class_=re.compile(r'ingredient|item', re.IGNORECASE))
            for item in potential_ingredients:
                text = item.get_text().strip()
                if text and len(text) < 200 and len(text) > 3:
                    ingredients.append(text)
        
        return ingredients

    def _extract_recipe_name(self, soup):
        """Extract recipe name from various sources"""
        # Try og:title first
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title.get('content').strip()
        
        # Try h1
        h1 = soup.find('h1')
        if h1:
            return h1.get_text().strip()
        
        # Try title tag
        title = soup.find('title')
        if title:
            return title.get_text().strip()
        
        return "Recipe"

    def _format_recipe_for_analysis(self, recipe_content):
        """Format extracted recipe data for AI analysis"""
        parts = []
        
        if recipe_content.get('name'):
            parts.append(f"Recipe Name: {recipe_content['name']}")
        
        if recipe_content.get('ingredients'):
            parts.append("Ingredients:")
            for ingredient in recipe_content['ingredients']:
                parts.append(f"- {ingredient}")
        
        if recipe_content.get('instructions'):
            parts.append("Instructions (first few steps):")
            for i, instruction in enumerate(recipe_content['instructions'][:3], 1):
                parts.append(f"{i}. {instruction}")
        
        return "\n".join(parts)

    def check_allergies_and_get_substitutes(self, recipe_content, recipe_url):
        """Use Llama3 to check for allergies and suggest substitutes"""
        if not self.allergies or not recipe_content:
            return [], {}
        
        allergies_list = ", ".join(self.allergies)
        
        prompt = f"""
You are an expert nutritionist. Analyze ONLY the ingredients list below and identify any ingredients that contain or may contain these allergens: {allergies_list}

{recipe_content}

IMPORTANT: 
- Only analyze the ingredients that are explicitly listed above
- Do not assume or add ingredients that are not listed
- Focus on the actual ingredient names, not cooking methods or instructions
- For each allergen found, suggest 2-3 practical substitutes

Please respond with ONLY a JSON object in this exact format:
{{
  "allergens_found": ["allergen1", "allergen2"],
  "substitutes": {{
    "allergen1": ["substitute1", "substitute2", "substitute3"],
    "allergen2": ["substitute1", "substitute2"]
  }}
}}

If no allergens are found, respond with:
{{
  "allergens_found": [],
  "substitutes": {{}}
}}

Be conservative - only flag ingredients that clearly contain the specified allergens.
"""

        try:
            response = ollama.chat(
                model='llama3',
                messages=[{'role': 'user', 'content': prompt}]
            )
            
            # Parse the response
            response_text = response['message']['content'].strip()
            
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                allergens_found = result.get('allergens_found', [])
                substitutes = result.get('substitutes', {})
                
                # Validate the data
                allergens_found = [a for a in allergens_found if isinstance(a, str)]
                validated_substitutes = {}
                for allergen, subs in substitutes.items():
                    if isinstance(subs, list):
                        validated_substitutes[allergen] = [s for s in subs if isinstance(s, str)]
                
                return allergens_found, validated_substitutes
            
        except Exception as e:
            print(f"Warning: Allergy check failed for {recipe_url}: {e}")
        
        return [], {}

    def format_allergy_info(self, allergens_found, substitutes):
        """Format allergy information and substitutes for calendar description"""
        if not allergens_found:
            return ""
        
        info_parts = [f"\n⚠️ ALLERGEN WARNING: Contains {', '.join(allergens_found)}"]
        
        if substitutes:
            info_parts.append("\n🔄 SUGGESTED SUBSTITUTES:")
            for allergen in allergens_found:
                if allergen in substitutes and substitutes[allergen]:
                    subs_text = ", ".join(substitutes[allergen])
                    info_parts.append(f"• {allergen.title()}: {subs_text}")
        
        return "".join(info_parts)

    def extract_title(self, url):
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            title = None
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                title = og_title.get('content').strip()
            if not title:
                scripts = soup.find_all('script', type='application/ld+json')
                for script in scripts:
                    try:
                        data = json.loads(script.string)
                        if isinstance(data, list):
                            for entry in data:
                                if isinstance(entry, dict) and ('Recipe' in str(entry.get('@type', ''))):
                                    if 'name' in entry:
                                        title = entry['name'].strip()
                                        break
                        elif isinstance(data, dict) and ('Recipe' in str(data.get('@type', ''))):
                            if 'name' in data:
                                title = data['name'].strip()
                                break
                    except Exception:
                        continue
            if not title:
                h1 = soup.find('h1')
                if h1 and h1.get_text():
                    title = h1.get_text().strip()
            if not title:
                title_tag = soup.find('title')
                if title_tag and title_tag.get_text():
                    title = title_tag.get_text().strip()
            if not title:
                path = urlparse(url).path
                filename = os.path.basename(path)
                filename = unquote(filename)
                filename = re.sub(r'[-_]', ' ', filename)
                filename = re.sub(r'\.html?$', '', filename, flags=re.IGNORECASE)
                title = filename.strip() or "Recipe"
            title = re.sub(r'\s*[-|]\s*(Recipe|Recipes|Cooking|Kitchen|Food).*$', '', title, flags=re.IGNORECASE)
            title = title.strip()
            if len(title) > 60:
                title = title[:57] + "..."
            return title
        except Exception:
            path = urlparse(url).path
            filename = os.path.basename(path)
            filename = unquote(filename)
            filename = re.sub(r'[-_]', ' ', filename)
            filename = re.sub(r'\.html?$', '', filename, flags=re.IGNORECASE)
            title = filename.strip() or "Recipe"
            return title

    def read_recipes_from_csv(self, filename):
        """Read recipes from CSV and shuffle them for better randomization"""
        recipes = []
        with open(filename, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                url = row['url'].strip()
                try:
                    days = int(row['days'])
                except:
                    days = 5
                recipes.append({'url': url, 'days': days})
        
        # Shuffle the recipes list to ensure random starting order
        random.shuffle(recipes)
        return recipes

    def get_next_recipe(self, recipes, prev_url, used_recent):
        """Get next recipe avoiding previous and recently used recipes"""
        
        # Level 1: Avoid previous and recent URLs
        level1_options = [recipe for recipe in recipes 
                         if (recipe['url'] != prev_url and 
                             recipe['url'] not in used_recent)]
        
        # Level 2: Just avoid previous URL
        level2_options = [recipe for recipe in recipes 
                         if recipe['url'] != prev_url]
        
        # Try each level, randomly selecting from available options
        if level1_options:
            return random.choice(level1_options)
        elif level2_options:
            return random.choice(level2_options)
        else:
            # Last resort: return random recipe
            return random.choice(recipes)

    def get_next_recipe_avoiding_conflict(self, recipes, prev_url, used_recent, conflicting_url):
        """Get next recipe while avoiding conflicts with the other meal type"""
        
        # Create lists of available recipes based on different restriction levels
        
        # Level 1: Avoid previous, recent, and conflicting URLs
        level1_options = [recipe for recipe in recipes 
                         if (recipe['url'] != prev_url and 
                             recipe['url'] not in used_recent and 
                             recipe['url'] != conflicting_url)]
        
        # Level 2: Avoid previous and conflicting URLs (ignore recent)
        level2_options = [recipe for recipe in recipes 
                         if (recipe['url'] != prev_url and 
                             recipe['url'] != conflicting_url)]
        
        # Level 3: Just avoid conflicting URL
        level3_options = [recipe for recipe in recipes 
                         if recipe['url'] != conflicting_url]
        
        # Try each level in order, randomly selecting from available options
        if level1_options:
            return random.choice(level1_options)
        elif level2_options:
            return random.choice(level2_options)
        elif level3_options:
            return random.choice(level3_options)
        else:
            # Last resort: return random recipe (shouldn't happen with 2+ recipes)
            return random.choice(recipes)

    def create_meal_prep_calendar(self, recipes, output_file="meal_prep_calendar.csv",
                                 start_date=None, num_weeks=4,
                                 first_lunch_url=None, first_dinner_url=None):
        
        # Push start date forward by one day by default
        if start_date is None:
            start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            start_date = start_date + timedelta(days=1)
        if len(recipes) < 2:
            print("Error: Need at least 2 recipes")
            return

        all_events = []
        prev_lunch_url = None
        prev_dinner_url = None
        lunch_used_recent = []
        dinner_used_recent = []
        
        # Cache for recipe content, allergies, and substitutes
        recipe_cache = {}
        
        # Track current active meals to avoid conflicts
        current_lunch_recipe = None
        current_dinner_recipe = None
        lunch_days_remaining = 0
        dinner_days_remaining = 0

        current_date = start_date
        total_days = num_weeks * 7

        # Process day by day to ensure lunch and dinner are never the same
        for day in range(total_days):
            meal_date = current_date + timedelta(days=day)
            
            # LUNCH PROCESSING
            if lunch_days_remaining <= 0:
                # Need a new lunch recipe
                if day == 0 and first_lunch_url:
                    recipe = next((r for r in recipes if r['url'] == first_lunch_url), None)
                    if not recipe:
                        print(f"Error: --first-lunch-url {first_lunch_url} not found in recipe list.")
                        sys.exit(1)
                else:
                    # Get next lunch recipe, ensuring it's different from current dinner
                    recipe = self.get_next_recipe_avoiding_conflict(
                        recipes, prev_lunch_url, lunch_used_recent[-2:], 
                        current_dinner_recipe['url'] if current_dinner_recipe else None
                    )
                
                current_lunch_recipe = recipe
                lunch_days_remaining = recipe['days']
                prev_lunch_url = recipe['url']
                lunch_used_recent.append(recipe['url'])
                
                # Get allergy info for lunch
                lunch_title = self.extract_title(recipe['url'])
                allergens_found = []
                substitutes = {}
                if self.allergies:
                    if recipe['url'] not in recipe_cache:
                        print(f"Checking allergies and finding substitutes for: {lunch_title}")
                        recipe_content = self.extract_recipe_content(recipe['url'])
                        allergens_found, substitutes = self.check_allergies_and_get_substitutes(recipe_content, recipe['url'])
                        recipe_cache[recipe['url']] = (allergens_found, substitutes)
                    else:
                        allergens_found, substitutes = recipe_cache[recipe['url']]
                
                allergy_info = self.format_allergy_info(allergens_found, substitutes)
                
                # Schedule prep event (on previous day if not first day)
                if day > 0:
                    prep_date = current_date + timedelta(days=day - 1)
                else:
                    prep_date = meal_date
                    
                all_events.append({
                    'Subject': f"Prep: Lunch - {lunch_title}",
                    'Start Date': prep_date.strftime('%m/%d/%Y'),
                    'Start Time': '',
                    'End Date': prep_date.strftime('%m/%d/%Y'),
                    'End Time': '',
                    'All Day Event': 'True',
                    'Description': f"Prep for lunch: {lunch_title}\n{recipe['url']}{allergy_info}",
                    'Location': 'Kitchen',
                    'Private': 'False'
                })
            
            # Schedule lunch meal event
            lunch_title = self.extract_title(current_lunch_recipe['url'])
            allergens_found, substitutes = recipe_cache.get(current_lunch_recipe['url'], ([], {}))
            allergy_info = self.format_allergy_info(allergens_found, substitutes)
            
            all_events.append({
                'Subject': f"Lunch: {lunch_title}",
                'Start Date': meal_date.strftime('%m/%d/%Y'),
                'Start Time': '',
                'End Date': meal_date.strftime('%m/%d/%Y'),
                'End Time': '',
                'All Day Event': 'True',
                'Description': f"Lunch - {lunch_title}\nRecipe: {current_lunch_recipe['url']}{allergy_info}",
                'Location': '',
                'Private': 'False'
            })
            
            lunch_days_remaining -= 1
            
            # DINNER PROCESSING
            if dinner_days_remaining <= 0:
                # Need a new dinner recipe
                if day == 0 and first_dinner_url:
                    recipe = next((r for r in recipes if r['url'] == first_dinner_url), None)
                    if not recipe:
                        print(f"Error: --first-dinner-url {first_dinner_url} not found in recipe list.")
                        sys.exit(1)
                else:
                    # Get next dinner recipe, ensuring it's different from current lunch
                    recipe = self.get_next_recipe_avoiding_conflict(
                        recipes, prev_dinner_url, dinner_used_recent[-2:], 
                        current_lunch_recipe['url'] if current_lunch_recipe else None
                    )
                
                current_dinner_recipe = recipe
                dinner_days_remaining = recipe['days']
                prev_dinner_url = recipe['url']
                dinner_used_recent.append(recipe['url'])
                
                # Get allergy info for dinner
                dinner_title = self.extract_title(recipe['url'])
                allergens_found = []
                substitutes = {}
                if self.allergies:
                    if recipe['url'] not in recipe_cache:
                        print(f"Checking allergies and finding substitutes for: {dinner_title}")
                        recipe_content = self.extract_recipe_content(recipe['url'])
                        allergens_found, substitutes = self.check_allergies_and_get_substitutes(recipe_content, recipe['url'])
                        recipe_cache[recipe['url']] = (allergens_found, substitutes)
                    else:
                        allergens_found, substitutes = recipe_cache[recipe['url']]
                
                allergy_info = self.format_allergy_info(allergens_found, substitutes)
                
                # Schedule prep event (on previous day if not first day)
                if day > 0:
                    prep_date = current_date + timedelta(days=day - 1)
                else:
                    prep_date = meal_date
                    
                all_events.append({
                    'Subject': f"Prep: Dinner - {dinner_title}",
                    'Start Date': prep_date.strftime('%m/%d/%Y'),
                    'Start Time': '',
                    'End Date': prep_date.strftime('%m/%d/%Y'),
                    'End Time': '',
                    'All Day Event': 'True',
                    'Description': f"Prep for dinner: {dinner_title}\n{recipe['url']}{allergy_info}",
                    'Location': 'Kitchen',
                    'Private': 'False'
                })
            
            # Schedule dinner meal event
            dinner_title = self.extract_title(current_dinner_recipe['url'])
            allergens_found, substitutes = recipe_cache.get(current_dinner_recipe['url'], ([], {}))
            allergy_info = self.format_allergy_info(allergens_found, substitutes)
            
            all_events.append({
                'Subject': f"Dinner: {dinner_title}",
                'Start Date': meal_date.strftime('%m/%d/%Y'),
                'Start Time': '',
                'End Date': meal_date.strftime('%m/%d/%Y'),
                'End Time': '',
                'All Day Event': 'True',
                'Description': f"Dinner - {dinner_title}\nRecipe: {current_dinner_recipe['url']}{allergy_info}",
                'Location': '',
                'Private': 'False'
            })
            
            dinner_days_remaining -= 1

        # Handle overflow days (meals that extend beyond the planned period)
        overflow_days = 0
        if lunch_days_remaining > 0 or dinner_days_remaining > 0:
            max_overflow = max(lunch_days_remaining, dinner_days_remaining)
            for extra_day in range(max_overflow):
                overflow_date = current_date + timedelta(days=total_days + extra_day)
                
                # Add remaining lunch days
                if lunch_days_remaining > 0:
                    lunch_title = self.extract_title(current_lunch_recipe['url'])
                    allergens_found, substitutes = recipe_cache.get(current_lunch_recipe['url'], ([], {}))
                    allergy_info = self.format_allergy_info(allergens_found, substitutes)
                    
                    all_events.append({
                        'Subject': f"Lunch: {lunch_title}",
                        'Start Date': overflow_date.strftime('%m/%d/%Y'),
                        'Start Time': '',
                        'End Date': overflow_date.strftime('%m/%d/%Y'),
                        'End Time': '',
                        'All Day Event': 'True',
                        'Description': f"Lunch - {lunch_title}\nRecipe: {current_lunch_recipe['url']}{allergy_info}",
                        'Location': '',
                        'Private': 'False'
                    })
                    lunch_days_remaining -= 1
                
                # Add remaining dinner days
                if dinner_days_remaining > 0:
                    dinner_title = self.extract_title(current_dinner_recipe['url'])
                    allergens_found, substitutes = recipe_cache.get(current_dinner_recipe['url'], ([], {}))
                    allergy_info = self.format_allergy_info(allergens_found, substitutes)
                    
                    all_events.append({
                        'Subject': f"Dinner: {dinner_title}",
                        'Start Date': overflow_date.strftime('%m/%d/%Y'),
                        'Start Time': '',
                        'End Date': overflow_date.strftime('%m/%d/%Y'),
                        'End Time': '',
                        'All Day Event': 'True',
                        'Description': f"Dinner - {dinner_title}\nRecipe: {current_dinner_recipe['url']}{allergy_info}",
                        'Location': '',
                        'Private': 'False'
                    })
                    dinner_days_remaining -= 1
                
                overflow_days += 1

        # Write CSV
        fieldnames = ['Subject', 'Start Date', 'Start Time', 'End Date', 'End Time',
                      'All Day Event', 'Description', 'Location', 'Private']
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_events)

        end_date = current_date + timedelta(days=total_days + overflow_days - 1)
        print(f"\nCSV file created: {output_file}")
        print(f"Created {len(all_events)} calendar events")
        print(f"Calendar runs from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        if self.allergies:
            print(f"Checked for allergies: {', '.join(self.allergies)}")
            print("Added substitute suggestions where allergens were found")
        print("✓ Ensured lunch and dinner are never the same recipe on any day")


def main():
    parser = argparse.ArgumentParser(
        description="Create rolling meal prep calendar with allergy checking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
CSV format (when using --file):
url,days
https://example.com/chicken-salad,3
https://example.com/beef-stew,2

Allergies file format (when using --allergies):
nuts
dairy
gluten
shellfish
        """
    )
    parser.add_argument('urls', nargs='*', help='Recipe URLs (or use --file)')
    parser.add_argument('--file', '-f', help='CSV file with columns: url,days')
    parser.add_argument('--allergies', '-a', help='Text file with allergies (one per line)')
    parser.add_argument('--output', '-o', default='meal_prep_calendar.csv', 
                       help='Output CSV filename')
    parser.add_argument('--start-date', help='Start date for first day (YYYY-MM-DD)')
    parser.add_argument('--weeks', '-w', type=int, default=4, 
                       help='Number of weeks to generate (default: 4)')
    parser.add_argument('--seed', type=int, help='Random seed for consistent results')
    parser.add_argument('--first-lunch-url', help='Force this URL as the first lunch')
    parser.add_argument('--first-dinner-url', help='Force this URL as the first dinner')
    args = parser.parse_args()

    # Parse start date
    start_date = None
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
        except ValueError:
            print("Invalid date format. Use YYYY-MM-DD")
            sys.exit(1)

    # Create generator instance first
    generator = MealPrepCalendarGenerator(seed=args.seed, allergies_file=args.allergies)
    
    # Get recipes using the class method
    if args.file:
        recipes = generator.read_recipes_from_csv(args.file)
    else:
        if not args.urls:
            print("No URLs provided. Use --help for usage information.")
            sys.exit(1)
        recipes = [{'url': url, 'days': 5} for url in args.urls]

    generator.create_meal_prep_calendar(
        recipes, args.output, start_date, args.weeks,
        first_lunch_url=args.first_lunch_url,
        first_dinner_url=args.first_dinner_url
    )
    
    print(f"\nTo import into Google Calendar:")
    print("1. Go to Google Calendar")
    print("2. Click the '+' next to 'Other calendars'")
    print("3. Select 'Import'")
    print(f"4. Upload the file: {args.output}")

if __name__ == "__main__":
    main()
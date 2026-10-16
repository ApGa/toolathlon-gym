I need help with a cook wellness analysis. There is external benchmark data available that I need you to fetch the data from http://localhost:30321/api/data.json and extract the relevant metrics.

Then search for recipes that match the requirements using our recipe database.

Use the terminal to create and run a Python script called cook_wellness_processor.py in the workspace that reads the collected data from JSON files you create, performs the analysis, and outputs cook_wellness_results.json.

Create an Excel file called Wellness_Report.xlsx with three sheets. The first sheet Data_Analysis should contain the main comparison data with relevant columns. The second sheet Metrics should summarize key metrics. The third sheet Recommendations should list actionable items.

The Data_Analysis sheet should include columns for the primary dimension (such as department, product, region, or topic), our internal metric values, the external benchmark values, and the gap or difference between them. Sort the data alphabetically by the primary dimension. The Metrics sheet should have two columns Metric and Value summarizing total counts, averages, and key statistics. The Recommendations sheet should list priority actions based on the gap analysis. Send an email to team-lead@company.com with subject "Analysis Report Complete" summarizing the key findings. Create a Notion page titled "Cook Wellness Dashboard" with a summary of the analysis.

Required workbook layout: Data_Analysis must have at least 5 data rows and columns Recipe, Category, Calories, Protein_g, Meets_Guidelines; Metrics must have at least 4 data rows and columns Metric, Value; Recommendations must have at least 2 data rows and columns Priority, Action. Additional columns are welcome for benchmark values and comparisons.

Use one row per recipe. If the recipe database does not contain nutritional measurements, label the Calories and Protein_g values as estimates and document the assumptions. Meets_Guidelines should explain how the recipe contributes to the supplied daily guidelines, not claim that every individual dish provides an entire daily allowance.

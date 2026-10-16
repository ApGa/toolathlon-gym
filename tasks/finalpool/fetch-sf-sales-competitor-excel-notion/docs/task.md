I need help with a sf competitor analysis. There is external benchmark data available that I need you to fetch the data from http://localhost:30335/api/data.json and extract the relevant metrics.

The endpoint supplies industry average salaries by department. Compare those benchmarks with the mean SALARY for each DEPARTMENT in HR_ANALYTICS.PUBLIC.EMPLOYEES from our company data warehouse.

Use the terminal to create and run a Python script called sf_competitor_processor.py in the workspace that reads the collected data from JSON files you create, performs the analysis, and outputs sf_competitor_results.json.

Create an Excel file called Sales_Competitor_Report.xlsx with three sheets. The first sheet Data_Analysis should contain the main comparison data with relevant columns. The second sheet Metrics should summarize key metrics. The third sheet Recommendations should list actionable items.

The Data_Analysis sheet should include Department, Internal_Avg_Salary, Industry_Avg, and Gap (internal average minus industry benchmark), with monetary values rounded to 2 decimals. Include all departments in the benchmark and sort the data alphabetically by Department. The Metrics sheet should have two columns Metric and Value summarizing total counts, averages, and key statistics. The Recommendations sheet should list priority actions based on the gap analysis. Create a Notion page titled "Sf Competitor Dashboard" with a summary of the analysis.

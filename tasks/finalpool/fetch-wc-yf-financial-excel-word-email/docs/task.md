I need help with a wc yf finance analysis. There is external benchmark data available that I need you to fetch the data from http://localhost:30333/api/data.json and extract the relevant metrics.

Then check our online store for current product and order data.

Use the terminal to create and run a Python script called wc_yf_finance_processor.py in the workspace that reads the collected data from JSON files you create, performs the analysis, and outputs wc_yf_finance_results.json.

Create an Excel file called Yf_Financial_Report.xlsx with three sheets. The first sheet Data_Analysis should contain the main comparison data with relevant columns. The second sheet Metrics should summarize key metrics. The third sheet Recommendations should list actionable items.

The Data_Analysis sheet should include columns for the primary dimension (such as department, product, region, or topic), our internal metric values, the external benchmark values, and the gap or difference between them. Sort the data alphabetically by the primary dimension. The Metrics sheet should have two columns Metric and Value summarizing total counts, averages, and key statistics. The Recommendations sheet should list priority actions based on the gap analysis. Also create a Word document called Yf_Financial_Analysis.docx with an executive summary, key findings, and recommendations sections. Send an email to team-lead@company.com with subject "Analysis Report Complete" summarizing the key findings.

Required workbook layout: Data_Analysis must have at least 6 data rows and columns Category, Product_Count, Our_Avg_Price, Total_Sales, Market_Avg_Price, Price_Gap_Pct; Metrics must have at least 3 data rows and columns Metric, Value; Recommendations must have at least 2 data rows and columns Priority, Action, Category. Additional columns are welcome for benchmark values and comparisons.

Use one row per category in the external benchmark. Our_Avg_Price is the mean current product price, Total_Sales is the sum of product total_sales, Market_Avg_Price comes from the benchmark, and Price_Gap_Pct is (Our_Avg_Price / Market_Avg_Price - 1) * 100.
